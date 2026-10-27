"""Real PostgreSQL durability, compare-and-swap, claims and tenant isolation."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from resource_ai.core.config import get_settings
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.workflow.store import PostgresCaseStore, WorkflowError

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


def test_durable_snapshots_claims_documents_and_scope_across_service_restart():
    url = get_settings().database_url
    schema = "re_ai_workflow_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        database = Database(make_conninfo(url, options=f"-c search_path={schema},public"))
        with database.connect(register_vector_types=False) as c:
            migration = Path("migrations/007_workflow_cases.sql").read_text("utf-8")
            c.execute(migration)
            c.execute(migration)
        store = PostgresCaseStore(database)
        assert store.claim_provider("synthetic-test-provider")
        assert not PostgresCaseStore(database).claim_provider("synthetic-test-provider")
        store.release_provider("synthetic-test-provider")
        assert not store.claim_provider("synthetic-test-provider")  # cooldown survives release
        state = store.create("tenant-a", {"test_only": True, "value": "initial"})
        key = state["case_id"]
        assert store.claim_ocr("tenant-a", key, "a" * 64)
        assert not PostgresCaseStore(database).claim_ocr("tenant-a", key, "a" * 64)
        first, snapshot, replay = store.reserve("tenant-a", key, 1, "request-one", {"step": 1})
        assert not replay
        assert PostgresCaseStore(database).get("tenant-a", key)["active_operation"] == first
        with pytest.raises(WorkflowError, match="in-progress"):
            PostgresCaseStore(database).reserve("tenant-a", key, 1, "request-two", {})
        with pytest.raises(WorkflowError, match="running-no-automatic-retry"):
            store.reserve("tenant-a", key, 1, "request-one", {"step": 1})
        snapshot["value"] = "updated"
        completed = store.complete("tenant-a", first, snapshot)
        assert store.list_cases("tenant-b") == []
        assert store.list_cases("tenant-a")[0]["case_id"] == key
        assert completed["revision"] == 2
        reloaded = PostgresCaseStore(database)
        _, previous, replay = reloaded.reserve("tenant-a", key, 1, "request-one", {"step": 1})
        assert replay and previous == completed
        with pytest.raises(WorkflowError, match="revision-conflict"):
            reloaded.reserve("tenant-a", key, 1, "old-revision", {})
        assert [x["revision"] for x in reloaded.history("tenant-a", key)] == [1, 2]
        store.put_document(
            "tenant-a", key, "fixture-doc", "text/plain", b"private-fixture", "0" * 64
        )
        assert reloaded.get_document("tenant-a", key, "fixture-doc") == (
            b"private-fixture",
            "text/plain",
        )
        with pytest.raises(WorkflowError, match="not-found"):
            reloaded.get_document("tenant-b", key, "fixture-doc")
        with pytest.raises(WorkflowError, match="not-found"):
            reloaded.get("tenant-b", key)
        op, _, _ = store.reserve("tenant-a", key, 2, "fails-once", {})
        store.fail("tenant-a", key, op)
        with pytest.raises(WorkflowError, match="failed-no-automatic-retry"):
            reloaded.reserve("tenant-a", key, 2, "fails-once", {})
    finally:
        assert schema.startswith("re_ai_workflow_test_") and len(schema) == 52
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
