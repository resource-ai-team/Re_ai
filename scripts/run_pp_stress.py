"""Production FastAPI over TCP + actual PostgreSQL/Neo4j, with isolated QA storage.

Uses synthetic business facts and optionally user-supplied documents. Never calls a
paid model, external OCR or road provider. Does not stop existing user services.
"""

import argparse
import base64
import hashlib
import json
import os
import random
import secrets
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
from pp_stress_cases import catalog, claim, claims, economic_scenario, scenario, stage, target
from psycopg import sql
from psycopg.conninfo import make_conninfo

from resource_ai.calculators.mass_balance import drying_balance, forward_balance, minimum_removal
from resource_ai.core.config import get_settings
from resource_ai.integrations.recycling_prices import load_snapshot, select_price
from resource_ai.material_quality.models import PerformanceStage

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_api():
    database_url = get_settings().database_url
    schema = "pp_stress_" + uuid4().hex
    child = None
    with psycopg.connect(database_url, connect_timeout=5) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped = make_conninfo(database_url, options=f"-c search_path={schema},public")
        with psycopg.connect(scoped, connect_timeout=5) as c:
            c.execute((ROOT / "migrations/007_workflow_cases.sql").read_text("utf-8"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "DATABASE_URL": scoped,
            "WORKFLOW_SERVICE_TOKEN": token,
            "SEMANTIC_CRITIC_ENABLED": "false",
            "SEMANTIC_CRITIC_ALLOW_EXTERNAL": "false",
            "WORKFLOW_OCR_ENABLED": "false",
            "WORKFLOW_OCR_ALLOW_EXTERNAL": "false",
            "ROAD_ROUTING_ENABLED": "false",
            "ROAD_ROUTING_ALLOW_EXTERNAL": "false",
            "LEGAL_GRAPH_MODEL_ENABLED": "false",
            "LEGAL_GRAPH_ALLOW_EXTERNAL": "false",
            "HF_HUB_OFFLINE": "1",
            "OMP_NUM_THREADS": "2",
        }
        log_path = ROOT / "data/local/pp-stress-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "resource_ai.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("isolated API exited before readiness; see private server log")
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("isolated API startup timed out")
        yield base, token
    finally:
        if child is not None and child.poll() is None:
            child.terminate()  # Only this runner's new child, never an existing service.
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        assert schema.startswith("pp_stress_") and len(schema) == 42
        with psycopg.connect(database_url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


class Bench:
    def __init__(self, base, token):
        self.client = httpx.Client(
            base_url=base,
            timeout=150,
            headers={
                "Authorization": "Bearer " + token,
                "X-Workflow-Tenant": "synthetic-stress-a",
                "X-Workflow-Actor": "synthetic-evaluator",
            },
            trust_env=False,
        )
        self.requests = 0
        self.latencies = []
        self.results = []
        self.only_prefix = None

    def request(self, method, path, **kwargs):
        start = time.perf_counter()
        response = self.client.request(method, path, **kwargs)
        self.requests += 1
        self.latencies.append(round((time.perf_counter() - start) * 1000, 2))
        return response

    def create(self, mode="simulation"):
        r = self.request(
            "POST",
            "/workflow/cases",
            json={
                "prompt": "Synthetic domestic rigid PP stress case, not a customer lot",
                "engine_request": None,
                "mode": mode,
                "assessment_on": "2026-10-30",
            },
        )
        assert r.status_code == 200, f"create HTTP {r.status_code}"
        return r.json()

    def write(self, state, action, payload, expected=200, *, key=None, tenant=None):
        method = "POST" if action in {"documents", "quality-research", "run"} else "PUT"
        headers = {"Idempotency-Key": key or str(uuid4())}
        if tenant:
            headers["X-Workflow-Tenant"] = tenant
        r = self.request(
            method,
            f"/workflow/cases/{state['case_id']}/{action}",
            headers=headers,
            json={"expected_revision": state["revision"], **payload},
        )
        assert r.status_code == expected, f"{action}: HTTP {r.status_code}, expected {expected}"
        return r.json() if r.status_code == 200 else state

    def assessment(self, state):
        r = self.request("GET", f"/workflow/cases/{state['case_id']}/pp-assessment")
        assert r.status_code == 200, f"assessment HTTP {r.status_code}"
        return r.json()

    def case(self, identifier, title, fn, *, real_document=False):
        if self.only_prefix and not identifier.startswith(self.only_prefix):
            return
        start = time.perf_counter()
        try:
            observed = fn() or {}
            self.results.append(
                {
                    "id": identifier,
                    "title": title,
                    "passed": True,
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                    "observed": observed,
                    "synthetic_business_data": True,
                    "real_document": real_document,
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolate cases and report sanitized failures.
            self.results.append(
                {
                    "id": identifier,
                    "title": title,
                    "passed": False,
                    "failure": str(exc)[:400]
                    if isinstance(exc, AssertionError)
                    else type(exc).__name__,
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                    "synthetic_business_data": True,
                    "real_document": real_document,
                }
            )

    def business(self, case):
        state = self.create(case.get("mode", "simulation"))
        if case["claims"]:
            code = case.get("input_http", 200)
            state = self.write(
                state,
                "quality-inputs",
                {"sample_id": "synthetic-sample", "claims": case["claims"]},
                code,
            )
            if code != 200:
                return {"http": code, "invalid_input_rejected": True}
        if not case.get("omit_target"):
            code = case.get("scenario_http", 200)
            state = self.write(state, "quality-scenario", {"scenario": case["scenario"]}, code)
            if code != 200:
                return {"http": code, "invalid_scenario_rejected": True}
        a = self.assessment(state)
        e = case["expected"]
        assert a["level"] == e["level"], f"level {a['level']} != {e['level']}"
        for key, expected in (
            ("mass_balance", e["mass_balance"]),
            ("drying_balance", e["drying_balance"]),
            ("economics", e["economics"]),
        ):
            assert (a[key] is not None) == expected, (
                f"{key} presence {(a[key] is not None)} != {expected}"
            )
        assert not a["recommended"] and not a["dispatch_approved"] and a["carbon_kgco2e"] is None
        if case.get("conflict"):
            assert a["profile"]["conflicts"] and a["status"] == "REVIEW"
        if case.get("expect_mode"):
            assert a["mode"] == case["expect_mode"], f"mode {a['mode']} != {case['expect_mode']}"
        if case["id"] == "cost-complete":
            assert Decimal(a["economics"]["net_cost_krw"]) == -192000, (
                "hand-calculated cost differs"
            )
        if case["id"] in {"all-pp-lost", "all-output-lost"}:
            assert a["forward_scenario"] is not None, "zero-output evidence disappeared"
            assert a["status"] == "REVIEW", "no PP recovery should require review"
        return {
            "level": a["level"],
            "status": a["status"],
            "mode": a["mode"],
            "issue_codes": [i["code"] for i in a["issues"]],
            "minimum_removal_kg": (a["mass_balance"] or {}).get("minimum_contaminant_removal_kg"),
            "recommended": a["recommended"],
            "net_cost_krw": (a["economics"] or {}).get("net_cost_krw"),
        }

    def prepared(self):
        state = self.write(
            self.create(), "quality-inputs", {"sample_id": "synthetic-sample", "claims": claims()}
        )
        return self.write(state, "quality-scenario", {"scenario": scenario()})

    def document(
        self, *, unit="wt%", result="20", sample="synthetic-sample", confirm=True, prefix=""
    ):
        state = self.write(
            self.create(),
            "quality-inputs",
            {"sample_id": "synthetic-sample", "claims": claims(None)},
        )
        text = f"PP 시험성적서\n시료 ID: {sample}\n습윤 질량 기준\n{prefix}오염률 {result} {unit}"
        state = self.write(
            state,
            "documents",
            {
                "filename": "synthetic-test-report.txt",
                "media_type": "text/plain",
                "base64_content": base64.b64encode(text.encode()).decode(),
            },
        )
        doc = next(iter(state["documents"].values()))
        if confirm:
            eid = doc["structured_report"]["claims"][0]["evidence_id"]
            state = self.write(
                state,
                "quality-confirmations",
                {
                    "evidence_ids": [eid],
                    "sample_id": "synthetic-sample",
                    "same_lot_confirmed": True,
                },
                422 if sample != "synthetic-sample" else 200,
            )
        state = self.write(state, "quality-scenario", {"scenario": scenario()})
        a = self.assessment(state)
        numeric = (
            confirm
            and sample == "synthetic-sample"
            and unit == "wt%"
            and result == "20"
            and not prefix
        )
        assert (a["mass_balance"] is not None) == numeric, (
            "ineligible report promoted or eligible report lost"
        )
        return {
            "level": a["level"],
            "document_type": doc["structured_report"]["document_type"],
            "rows": len(doc["structured_report"]["rows"]),
            "calculated": a["mass_balance"] is not None,
        }

    def integrity(self):
        state = self.prepared()
        key = str(uuid4())
        payload = {"claims": [claim("moisture", 1)]}
        after = self.write(state, "quality-inputs", payload, key=key)
        replay = self.write(state, "quality-inputs", payload, key=key)
        assert replay["revision"] == after["revision"], "replay wrote a duplicate revision"
        self.write(state, "quality-inputs", {"claims": [claim("moisture", 2)]}, 409, key=key)
        self.write(state, "quality-inputs", payload, 409)
        self.write(after, "quality-inputs", {"claims": [claim("contamination", 10)]}, 409)
        self.write(
            after,
            "quality-inputs",
            {"claims": [claim("moisture", 1, evidence_id="foreign-write")]},
            404,
            tenant="synthetic-stress-b",
        )
        r = self.request(
            "GET",
            f"/workflow/cases/{state['case_id']}/waste-profile",
            headers={"X-Workflow-Tenant": "synthetic-stress-b"},
        )
        assert r.status_code == 404
        r = self.request(
            "GET",
            f"/workflow/cases/{state['case_id']}",
            headers={"Authorization": "Bearer deliberately-wrong"},
        )
        assert r.status_code == 401
        return {
            "idempotent_replay": True,
            "stale_revision_rejected": True,
            "immutable_claims": True,
            "tenant_isolation": True,
        }

    def concurrent_writes(self):
        state = self.prepared()
        path = f"/workflow/cases/{state['case_id']}/quality-inputs"

        def write(value):
            return self.client.put(
                path,
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "expected_revision": state["revision"],
                    "claims": [claim("moisture", value, evidence_id="race-" + str(value))],
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(write, (1, 2)))
        self.requests += 2
        assert statuses == [200, 409], f"concurrent statuses {statuses}"
        return {"statuses": statuses}

    def comparison(self):
        state = self.prepared()
        path = f"/workflow/cases/{state['case_id']}"
        before = self.request("GET", path).json()
        r = self.request(
            "POST",
            path + "/pp-scenarios",
            json={
                "expected_revision": state["revision"],
                "scenarios": [
                    economic_scenario(),
                    scenario(target=target(limits=[{"field": "contamination", "max_value": "10"}])),
                ],
            },
        )
        assert r.status_code == 200 and len(r.json()["results"]) == 2
        after = self.request("GET", path).json()
        assert (
            before["revision"] == after["revision"]
            and before["material_quality"] == after["material_quality"]
        )
        assert r.json()["pareto_fronts"] == [] and not r.json()["persisted"]
        return {"compared": 2, "read_only": True, "invented_carbon_or_pareto": False}

    def real_document(self, path):
        assert path.is_file(), "provided document unavailable; OCR not run"
        content = path.read_bytes()
        state = self.write(
            self.create(),
            "documents",
            {
                "filename": "provided-report.png",
                "media_type": "image/png",
                "base64_content": base64.b64encode(content).decode(),
                "use_local_image_ocr": True,
            },
        )
        doc = next(iter(state["documents"].values()))
        a = self.assessment(state)
        assert a["level"] == 0 and a["mass_balance"] is None
        r = self.request(
            "GET", f"/workflow/cases/{state['case_id']}/documents/{doc['document_id']}"
        )
        assert (
            r.status_code == 200
            and hashlib.sha256(r.content).hexdigest() == hashlib.sha256(content).hexdigest()
        )
        return {
            "availability": "provided_document",
            "ocr_status": doc["extraction"]["status"],
            "document_type": doc["structured_report"]["document_type"],
            "table_rows": len(doc["structured_report"]["rows"]),
            "level": a["level"],
            "raw_round_trip": True,
            "external_transmission": False,
        }

    def research(self, form="rigid_scrap", mutate=None):
        state = self.write(
            self.create(),
            "quality-inputs",
            {
                "sample_id": "synthetic-sample",
                "claims": claims(None, material_form={"text_value": form}),
            },
        )
        origin = {
            "organisation_id": "synthetic-org",
            "site_id": "synthetic-site",
            "label": "Synthetic QA manufacturing site",
            "pickup_address": "Synthetic pickup only; not an actual factory",
            "latitude": 37.2,
            "longitude": 127.0,
        }
        state = self.write(
            state,
            "quality-research",
            {
                "origin": origin,
                "max_plan_pages": 1,
                "max_legal_queries": 1,
                "max_technical_queries": 0,
            },
        )
        a = self.assessment(state)
        assert a["kg_candidates_loaded"], "new research binding missing"
        candidates = state["engine"]["review"]["research"]["candidates"]
        if mutate:
            inputs = deepcopy(state["inputs"])
            if mutate == "quantity":
                inputs["engine_request"]["lot"]["quantity_kg"] = 2000
            else:
                inputs["engine_request"]["origin"].update(site_id="different-site", latitude=36.8)
            state = self.write(state, "inputs", {"inputs": inputs})
            assert not self.assessment(state)["kg_candidates_loaded"], (
                "KG binding survived changed engine request"
            )
        return {
            "form": form,
            "candidate_count": len(candidates),
            "binding_invalidated_after": mutate,
            "recommended": False,
        }


def numeric_properties(count):
    rng = random.Random(20261030)
    failures = []
    for i in range(count):
        mass = Decimal(rng.randint(1, 1000000000)) / 100
        contaminant = Decimal(rng.randint(0, 9999)) / 100
        limit = Decimal(rng.randint(0, 9999)) / 100
        loss = Decimal(rng.randint(0, 9999)) / 100
        try:
            result = minimum_removal(mass, contaminant, limit, loss)
            with localcontext() as ctx:
                ctx.prec = 80
                output = Decimal(result["output_mass_upper_bound_kg"])
                residue = Decimal(result["residue_kg"])
                pp = mass * (1 - contaminant / 100) * (1 - loss / 100)
                tolerance = max(Decimal("1e-24"), mass * Decimal("1e-35"))
                assert 0 <= output <= mass and residue >= 0
                assert abs(output + residue - mass) <= tolerance
                assert output + tolerance >= pp
                assert output - pp <= output * limit / 100 + tolerance
                low = minimum_removal(mass, contaminant, limit, Decimal(0))
                assert Decimal(low["residue_kg"]) <= residue + tolerance
                rates = [
                    PerformanceStage.model_validate(
                        stage(
                            stage_id=f"s{k}",
                            contaminant_removal_pct=str(rng.randint(0, 100)),
                            pp_loss_pct=str(rng.randint(0, 100)),
                        )
                    )
                    for k in range(rng.randint(1, 4))
                ]
                forward = forward_balance(mass, contaminant, rates)
                assert forward["mass_conserved"]
                expected_pp = mass * (1 - contaminant / 100)
                expected_c = mass * contaminant / 100
                for s in rates:
                    expected_pp *= 1 - s.pp_loss_pct / 100
                    expected_c *= 1 - s.contaminant_removal_pct / 100
                assert abs(Decimal(forward["pp_kg"]) - expected_pp) <= tolerance
                assert abs(Decimal(forward["contaminant_kg"]) - expected_c) <= tolerance
                moisture = Decimal(rng.randint(0, 9999)) / 100
                target_moisture = moisture * Decimal(rng.randint(0, 100)) / 100
                dry = drying_balance(mass, moisture, target_moisture)
                assert (
                    abs(Decimal(dry["final_wet_mass_kg"]) + Decimal(dry["water_removal_kg"]) - mass)
                    <= tolerance
                )
        except AssertionError:
            failures.append(
                {
                    "iteration": i,
                    "mass": str(mass),
                    "contamination": str(contaminant),
                    "target": str(limit),
                    "loss": str(loss),
                }
            )
    return {
        "generated_cases": count,
        "passed": count - len(failures),
        "failures": failures[:20],
        "seed": 20261030,
        "independent_invariants_per_case": 9,
        "synthetic": True,
    }


def price_properties():
    root = ROOT / "data/curated/recycling_prices"
    manifest = json.loads((root / "keco-2026-10.manifest.json").read_text("utf-8"))
    snapshot = load_snapshot(root / "keco-2026-10.csv", manifest)
    assert snapshot["status"] == "loaded"
    records = [r for r in snapshot["records"] if "-PP(" in r["item"]]
    checks = 0
    for row in records:
        assert (
            select_price(snapshot, period=row["period"], region=row["region"], item=row["item"])
            == row
        )
        checks += 1
        assert (
            select_price(snapshot, period="2026-09", region=row["region"], item=row["item"])[
                "price_krw_per_kg"
            ]
            is None
        )
        checks += 1
        assert not row["is_quote"]
        checks += 1
    return {
        "real_source_csv": True,
        "pp_observation_slots": len(records),
        "checks": checks,
        "sha256": snapshot["sha256"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/local/pp-stress-baseline.json")
    parser.add_argument("--numeric-cases", type=int, default=3000)
    parser.add_argument("--kg", action="store_true")
    parser.add_argument("--only-prefix", default=None)
    parser.add_argument("--document", action="append", default=[])
    args = parser.parse_args()
    started = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    start = time.perf_counter()
    numeric = numeric_properties(args.numeric_cases)
    prices = price_properties()
    with isolated_api() as (base, token):
        b = Bench(base, token)
        b.only_prefix = args.only_prefix
        for case in catalog():
            b.case(case["id"], case["title"], lambda case=case: b.business(case))
        for name, options in (
            ("confirmed", {}),
            ("unconfirmed", {"confirm": False}),
            ("other-sample", {"sample": "different-sample"}),
            ("migration", {"unit": "mg/L"}),
            ("nd", {"result": "ND"}),
            ("upper-bound", {"result": "<0.1"}),
            ("food-contact", {"prefix": "식품용 용출 시험\n"}),
        ):
            b.case(
                "document-" + name,
                "시험성적서 입력 경계: " + name,
                lambda options=options: b.document(**options),
            )
        b.case("integrity", "멱등키·버전·출처 불변·tenant·인증", b.integrity)
        for index in range(8):
            b.case(f"concurrency-{index}", "같은 revision에 동시 입력", b.concurrent_writes)
        b.case("read-only-comparison", "여러 시나리오 비교의 원본 불변", b.comparison)
        for i, path in enumerate(args.document):
            b.case(
                f"provided-document-{i + 1}",
                "사용자 제공 원본 로컬 OCR",
                lambda path=path: b.real_document(Path(path)),
                real_document=True,
            )
        if args.kg:
            for form in ("rigid_scrap", "rigid_part", "flake", "bale", "pellet"):
                b.case("kg-" + form, "실제 Neo4j/RAG: " + form, lambda form=form: b.research(form))
            for changed in ("quantity", "origin"):
                b.case(
                    "kg-stale-" + changed,
                    "조사 후 " + changed + " 변경",
                    lambda changed=changed: b.research(mutate=changed),
                )
        result = {
            "started_kst": started,
            "duration_seconds": round(time.perf_counter() - start, 2),
            "production_app_over_real_tcp": True,
            "real_postgresql_isolated_schema": True,
            "real_neo4j_rag_requested": args.kg,
            "paid_external_calls_enabled": False,
            "synthetic_business_data": True,
            "industrial_recommendation_accuracy_claimed": False,
            "numeric": numeric,
            "prices": prices,
            "api": {
                "cases": len(b.results),
                "passed": sum(r["passed"] for r in b.results),
                "requests": b.requests,
                "max_request_ms": max(b.latencies, default=0),
                "results": b.results,
            },
        }
        b.client.close()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    failures = [r for r in result["api"]["results"] if not r["passed"]]
    print(
        json.dumps(
            {
                "api_cases": result["api"]["cases"],
                "api_passed": result["api"]["passed"],
                "requests": result["api"]["requests"],
                "numeric_passed": numeric["passed"],
                "numeric_total": args.numeric_cases,
                "failures": [{"id": r["id"], "failure": r["failure"]} for r in failures],
                "output": str(path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 1 if failures or numeric["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
