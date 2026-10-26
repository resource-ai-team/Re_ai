"""One explicit, small Azure Astra/high Critic call against the PP replay.

Only a public-derived route and labelled synthetic numbers leave the server.
No automatic retries, private documents, names, coordinates or raw LCI data.
"""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from resource_ai.agents.semantic_critic import SemanticCriticService
from resource_ai.core.config import get_settings
from resource_ai.evidence.critic_models import SemanticCriticOutput
from resource_ai.integrations.azure_model import AzureResponsesModel, strict_schema
from resource_ai.knowledge_graph.ontology import digest
from resource_ai.legal_graph.llm import OUTPUTS, TASKS
from resource_ai.legal_graph.models import LegalGraphError

MAX_OUTPUT = 1024
MAX_INPUT_UPPER_TOKENS = 6256


def focused_packet(trace, phase):
    if phase not in {"before", "after"}:
        raise ValueError("Unknown focused phase")
    if trace.get("schema") != "critic-revision-replay-v1" or not trace.get("all_passed"):
        raise ValueError("A successful labelled code replay is required")
    canonical = trace["rounds"][-1]["focus"]
    supplied = trace["rounds"][0 if phase == "before" else -1]["focus"]
    ids = {s["id"]: f"S{i + 1}" for i, s in enumerate(canonical["stages"])}
    expected = [{"from": ids[e["from"]], "to": ids[e["to"]]} for e in canonical["handoffs"]]
    observed = [{"from": ids[e["from"]], "to": ids[e["to"]]} for e in supplied["handoffs"]]
    unit = supplied["transport_line"]
    # Before: deliberately wrong draft arithmetic, NOT a Python engine result.
    # After: normalized draft value; the code replay independently rechecked it.
    claimed = str(Decimal(unit["activity"]) * Decimal(unit["factor"]))
    values = [
        {
            "source": "public KG snapshot",
            "stages": [{"id": ids[s["id"]], "process": s["label"]} for s in canonical["stages"]],
            "handoffs": expected,
        },
        {
            "source": "synthetic quote and lot",
            "mass_kg": 3000,
            "quote": "20000 KRW/t",
            "permit_and_intake": "unknown",
        },
        {
            "source": "unverified draft with injected faults"
            if phase == "before"
            else "revised draft",
            "handoffs": observed,
            "activity": unit["activity"],
            "activity_unit": unit["activity_unit"],
            "factor": unit["factor"],
            "factor_unit": unit["factor_unit"],
            "claimed_transport_KRW": claimed,
        },
    ]
    records = {
        f"E{i + 1:04d}": {
            "ref_id": f"E{i + 1:04d}",
            "candidate_id": "A",
            "target_id": "A",
            "kind": "engine_record",
            "metadata": {},
            "evidence_id": f"focused-public-synthetic:{i + 1}",
            "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        }
        for i, value in enumerate(values)
    }
    targets = {"A": {"candidate": {"A"}, "stage": set(ids.values()), "handoff": set()}}
    payload = {
        "max_findings": 2,
        "coverage": {
            "candidate_count": 1,
            "scope": "KG continuity and dimensional cost only",
            "other_candidates_and_legal_audit": "outside scope, still unverified",
            "numeric_values": "synthetic",
            "code_findings_supplied": False,
        },
        "allowed_targets": {"A": {k: sorted(v) for k, v in targets["A"].items()}},
        "evidence": [{k: v for k, v in r.items() if k != "evidence_id"} for r in records.values()],
    }
    return payload, records, targets


def input_upper_tokens(payload):
    # UTF-8 bytes conservatively bound text tokens; extra allowance for framing.
    text = json.dumps(
        {
            "input": payload,
            "instructions": TASKS["focused_critic"],
            "format": strict_schema(OUTPUTS["focused_critic"].model_json_schema()),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return len(text.encode("utf-8")) + 256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-public-synthetic", action="store_true", required=True)
    parser.add_argument("--phase", choices=["before", "after"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before-receipt", type=Path)
    parser.add_argument(
        "--ledger", type=Path, default=Path("data/local/azure-critic-budget-2026-10-27.json")
    )
    args = parser.parse_args()
    trace = json.loads(args.source.read_text(encoding="utf-8"))
    payload, records, targets = focused_packet(trace, args.phase)
    reference_basis = digest(payload["evidence"][:2])
    if args.phase == "after":
        if args.before_receipt is None:
            raise ValueError("After-phase requires the successful before receipt")
        before = json.loads(args.before_receipt.read_text(encoding="utf-8"))
        if (
            before.get("status") != "completed"
            or before.get("rejected_findings")
            or before.get("call", {}).get("verified_model") != "gpt-6-astra"
            or digest(before["evidence"][:2]) != reference_basis
        ):
            raise ValueError("The before receipt or KG/quote basis differs; no request sent")
    bound = input_upper_tokens(payload)
    if bound > MAX_INPUT_UPPER_TOKENS:
        raise ValueError("Focused input exceeds the small live-test budget")
    ledger = (
        json.loads(args.ledger.read_text(encoding="utf-8"))
        if args.ledger.exists()
        else {
            "schema": "focused-azure-budget-v1",
            "approved_test_cap_krw": 1000,
            "fx_buffer_krw_per_usd": 2000,
            "prior_connection_reserve_usd": "0.23",
            "attempts": [],
            "note": "Conservative planning estimates, not a verified Azure bill or exchange quote",
        }
    )
    if len(ledger["attempts"]) >= 2 or any(a["phase"] == args.phase for a in ledger["attempts"]):
        raise ValueError(
            "This test allows at most one attempt per phase, two total; no automatic retry"
        )
    reserve = Decimal(bound) * Decimal(10) / 1000000 + Decimal(MAX_OUTPUT) * 50 / 1000000
    total = (
        Decimal(ledger["prior_connection_reserve_usd"])
        + reserve
        + sum(Decimal(a["reserved_usd"]) for a in ledger["attempts"])
    )
    if total * ledger["fx_buffer_krw_per_usd"] > ledger["approved_test_cap_krw"]:
        raise ValueError("Estimated cumulative test ceiling exceeded; no request sent")
    entry = {
        "phase": args.phase,
        "reserved_usd": str(reserve),
        "input_token_upper_bound": bound,
        "max_output_tokens": MAX_OUTPUT,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "reserved",
    }
    ledger["attempts"].append(entry)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    settings = get_settings()
    model = AzureResponsesModel(
        settings.azure_openai_api_key.get_secret_value(),
        endpoint=settings.azure_openai_endpoint,
        deployment=settings.azure_openai_deployment,
        deployment_model=settings.azure_openai_deployment_model,
        reasoning_effort="high",
        max_output_tokens=MAX_OUTPUT,
        timeout=180,
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": args.phase,
        "model": "gpt-6-astra",
        "reasoning_effort": "high",
        "live_model_calls": 1,
        "scope": "One public-derived route and synthetic numbers; not a full legal or product evaluation",
        "source_sha256": digest(trace),
        "reference_basis_sha256": reference_basis,
        "input_sha256": digest(payload),
        "evidence": [{k: v for k, v in r.items() if k != "evidence_id"} for r in records.values()],
        "concerns": [],
        "rejected_findings": [],
        "dispatch_approved": False,
        "planning_cost_ceiling_krw_including_prior_reserve": str(
            total * ledger["fx_buffer_krw_per_usd"]
        ),
    }
    try:
        output = model.complete("focused_critic", payload)
        parsed = SemanticCriticOutput.model_validate(output)
        if len(parsed.concerns) > payload["max_findings"]:
            raise LegalGraphError("model-output", "Finding count exceeds the requested bound")
        for index, concern in enumerate(parsed.concerns):
            reason = SemanticCriticService._check(concern, records, targets)
            if reason:
                report["rejected_findings"].append({"index": index, "reason": reason})
            else:
                report["concerns"].append(concern.model_dump(mode="json"))
        report["status"] = "completed" if not report["rejected_findings"] else "partial"
    except LegalGraphError as error:
        report["status"], report["error_code"] = "failed", error.code
    report["call"] = model.last_call
    entry.update({"status": report["status"], "usage": model.last_call.get("usage", {})})
    args.ledger.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: report[k] for k in ("phase", "status", "concerns", "rejected_findings", "call")},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
