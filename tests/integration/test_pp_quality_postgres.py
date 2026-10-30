"""Synthetic A/B/C over real PostgreSQL and HTTP contracts, with injected Mock OCR."""

import base64
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from resource_ai.api.routes.workflow import get_case_service, get_case_store, identity
from resource_ai.core.config import get_settings
from resource_ai.document_processing.adapters import MockLayoutOCR
from resource_ai.main import app
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.workflow.models import CaseCreate
from resource_ai.workflow.service import CaseService
from resource_ai.workflow.store import PostgresCaseStore
from tests.unit.test_pp_quality_pipeline import claim, layout, target

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


@pytest.mark.parametrize("scenario", ["A_document", "B_manual", "C_conflict"])
def test_real_database_http_case_pipeline_and_tenant_separation(scenario):
    url = get_settings().database_url
    schema = "re_ai_pp_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        database = Database(make_conninfo(url, options=f"-c search_path={schema},public"))
        with database.connect(register_vector_types=False) as c:
            c.execute(Path("migrations/007_workflow_cases.sql").read_text("utf-8"))
        store = PostgresCaseStore(database)
        service = CaseService(
            store,
            lambda _: pytest.fail("Unrequested KG/LLM call"),
            local_ocr=MockLayoutOCR(layout()),
        )
        state = service.create(
            "tenant-a",
            CaseCreate(
                prompt="synthetic PP request", mode="simulation", assessment_on=date(2026, 10, 29)
            ),
        )
        case_id = state["case_id"]
        app.dependency_overrides[get_case_store] = lambda: store
        app.dependency_overrides[get_case_service] = lambda: service
        app.dependency_overrides[identity] = lambda: "tenant-a"
        with TestClient(app) as client:
            path = f"/workflow/cases/{case_id}"

            def put(action, body):
                nonlocal state
                response = client.put(
                    path + "/" + action,
                    headers={"Idempotency-Key": str(uuid4())},
                    json={"expected_revision": state["revision"], **body},
                )
                assert response.status_code == 200, response.text
                state = response.json()

            if scenario != "B_manual":
                r = client.post(
                    path + "/documents",
                    headers={"Idempotency-Key": str(uuid4())},
                    json={
                        "expected_revision": state["revision"],
                        "filename": "synthetic.png",
                        "media_type": "image/png",
                        "base64_content": base64.b64encode(
                            b"\x89PNG\r\n\x1a\nsynthetic-not-an-industrial-document"
                        ).decode(),
                        "use_local_image_ocr": True,
                    },
                )
                assert r.status_code == 200, r.text
                state = r.json()
            claims = [
                claim("material", "PP"),
                claim("material_form", "rigid_scrap"),
                claim("quantity_kg", 1000),
            ]
            if scenario == "C_conflict":
                claims.append(claim("contamination", 10))
            put("quality-inputs", {"sample_id": "synthetic-sample-a", "claims": claims})
            if scenario == "A_document":
                doc = next(iter(state["documents"].values()))
                eid = doc["structured_report"]["claims"][0]["evidence_id"]
                assert client.get(path + "/pp-assessment").json()["level"] == 1
                put(
                    "quality-confirmations",
                    {
                        "evidence_ids": [eid],
                        "sample_id": "synthetic-sample-a",
                        "same_lot_confirmed": True,
                    },
                )
            put("quality-scenario", {"scenario": {"target": target()}})
            assessment = client.get(path + "/pp-assessment").json()
            if scenario == "A_document":
                assert assessment["level"] == 2 and assessment["mass_balance"]
            elif scenario == "B_manual":
                assert assessment["level"] == 1 and assessment["mass_balance"] is None
            else:
                assert assessment["status"] == "REVIEW" and assessment["profile"]["conflicts"]
            assert not assessment["recommended"]
            # Restarted storage and another authenticated tenant must not expose originals/claims.
            assert PostgresCaseStore(database).get("tenant-a", case_id)["material_quality"]
            app.dependency_overrides[identity] = lambda: "tenant-b"
            assert client.get(path + "/waste-profile").status_code == 404
    finally:
        app.dependency_overrides.clear()
        assert schema.startswith("re_ai_pp_test_") and len(schema) == 46
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
