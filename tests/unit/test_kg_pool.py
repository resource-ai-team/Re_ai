from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.agents.tools.graph_tool import get_candidate_pool_page
from resource_ai.api.schemas.kg_pool import CandidatePoolRequest
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.pool import CandidatePool, decode_cursor, encode_cursor
from resource_ai.main import app


@pytest.mark.parametrize("material", [None, "unknown", "미확인", "모름"])
def test_missing_material_does_not_query_db_or_invent_existing_treatment(material):
    with patch.object(store, "_read", side_effect=AssertionError("No DB expected")):
        result = CandidatePool().search(CandidatePoolRequest(material=material))
    assert result.status == "needs_input"
    assert result.input_context["current_treatment"] is None
    assert result.items == []
    assert result.dispatch_approved is False


def test_external_view_never_echoes_customer_context_even_without_db():
    result = CandidatePool().search(
        CandidatePoolRequest(
            material="PETroleum", region="private-factory", audience="external_model"
        )
    )
    assert result.status == "unsupported_material"
    assert result.input_context == {}
    assert "private-factory" not in result.model_dump_json()


@pytest.mark.parametrize("token", ["%%%", "e30", "W10", "bnVsbA", "wA", "a"])
def test_malformed_cursor_is_a_public_bad_request_not_an_internal_error(token):
    with pytest.raises(store.GraphStoreError) as error:
        decode_cursor(token)
    assert error.value.code == "invalid-cursor"


def test_cursor_rejects_boolean_bucket_and_extra_properties():
    value = {
        "v": 1,
        "generation": "g",
        "namespace": "n",
        "request": "hash",
        "bucket": True,
        "id": "row",
    }
    with pytest.raises(store.GraphStoreError):
        decode_cursor(encode_cursor(value))
    value["bucket"] = 0
    assert decode_cursor(encode_cursor(value)) == value
    value["cypher"] = "arbitrary-query"
    with pytest.raises(store.GraphStoreError):
        decode_cursor(encode_cursor(value))


@pytest.mark.parametrize(
    "payload",
    [
        {"page_size": 0},
        {"page_size": 101},
        {"top_k": 3},
        {"cypher": "MATCH (n)"},
        {"quantity_kg": -1},
        {"cursor": "x" * 2049},
        {"priority": "carbon"},
    ],
)
def test_pool_contract_has_no_premature_top_k_or_user_priority(payload):
    with pytest.raises(ValidationError):
        CandidatePoolRequest.model_validate(payload)


@pytest.mark.parametrize(
    "code,status",
    [("invalid-cursor", 400), ("stale-cursor", 409), ("unavailable", 503), ("integrity", 503)],
)
def test_api_distinguishes_restartable_pagination_errors_from_db_failure(code, status):
    class Failure:
        def candidate_pool(self, request):
            raise store.GraphStoreError(code, "safe public message")

    app.dependency_overrides[get_graph_query] = Failure
    try:
        response = TestClient(app).post("/kg/candidate-pool", json={"material": "PET"})
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code
    finally:
        app.dependency_overrides.clear()


def test_agent_tool_always_uses_external_view_even_if_caller_requests_internal():
    class Recorder:
        def candidate_pool(self, request):
            assert request.audience == "external_model"
            return CandidatePool().search(request)

    with patch("resource_ai.agents.tools.graph_tool.get_graph_query", return_value=Recorder()):
        result = get_candidate_pool_page(audience="internal")
    assert result["input_context"] == {}
    assert result["status"] == "needs_input"
