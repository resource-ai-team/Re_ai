"""Contract/adversarial fixtures only; they do not measure GPT legal reasoning."""

import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
from resource_ai.agents.semantic_critic import SemanticCriticService, get_semantic_critic_service
from resource_ai.agents.site_review import SiteReviewService
from resource_ai.api.routes.analysis import get_research_service
from resource_ai.api.schemas.decision_review import DecisionReviewRequest
from resource_ai.core.config import Settings
from resource_ai.evidence.critic_models import SemanticCriticOptions
from resource_ai.integrations.azure_model import AzureResponsesModel
from resource_ai.main import app
from tests.unit.test_candidate_research import FakeGraph, FakeRepository
from tests.unit.test_decision_review import setup
from tests.unit.test_site_discovery import Graph
from tests.unit.test_site_review import scenario


class Model:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def complete(self, task, payload):
        assert task == "semantic_critic"
        self.calls.append(payload)
        output = {"concerns": [concern(payload)]}
        return self.transform(output, payload) if self.transform else output


def concern(payload):
    pid = next(iter(payload["allowed_targets"]))
    source = next(r for r in payload["evidence"] if r["candidate_id"] == pid)
    return {
        "stage": "validation",
        "kind": "potential_error",
        "candidate_id": pid,
        "target_type": "stage",
        "target_id": payload["allowed_targets"][pid]["stage"][0],
        "message": "이번 물량의 인수 조건을 확인해야 합니다.",
        "next_action": "현행 허가와 이번 로트의 서면 인수 조건을 확인하세요.",
        "citations": [{"ref_id": source["ref_id"], "quote": source["text"][:100]}],
    }


def critic(model=None, **fields):
    return SemanticCriticService(
        config=Settings(
            _env_file=None,
            semantic_critic_enabled=True,
            semantic_critic_allow_external=True,
            semantic_critic_service_token="synthetic-internal-token",
            **fields,
        ),
        model_factory=lambda: model or Model(),
    )


def execute(model=None, **fields):
    research, request, bound = setup()
    return (
        DecisionReviewService(research)
        .set_semantic_service(critic(model, **fields))
        .run(DecisionReviewRequest(research=request, numeric=bound, semantic_critic={}))
    )


def test_optional_model_runs_once_keeps_code_checks_numbers_and_never_grants_pass():
    model = Model()
    response = execute(model)
    assert len(model.calls) == 1 and len(model.calls[0]["allowed_targets"]) == 3
    assert response.semantic_critic.status == "completed"
    assert response.semantic_critic.semantic_correctness_verified is False
    assert len(response.numeric_review["included_candidate_ids"]) == 3
    assert response.route == "REVIEW" and not response.router["pass_gate_enabled"]
    assert response.final_recommendations == [] and not response.dispatch_approved
    semantic = [i for i in response.critic if i.origin == "model_concern"]
    assert len(semantic) == 1 and semantic[0].severity == "missing"
    assert semantic[0].semantic_finding_id
    assert any(m["origin"] == "model_concern" and m["on_map"] for m in response.map_markers)


def test_not_requested_or_disabled_or_external_denied_never_constructs_provider():
    research, request, _ = setup()
    service = SemanticCriticService(
        config=Settings(_env_file=None),
        model_factory=lambda: pytest.fail("must not construct provider"),
    )
    reviewer = DecisionReviewService(research).set_semantic_service(service)
    assert (
        reviewer.run(DecisionReviewRequest(research=request)).semantic_critic.status
        == "not_requested"
    )
    requested = DecisionReviewRequest(research=request, semantic_critic={})
    assert reviewer.run(requested).semantic_critic.status == "disabled"
    service.config.semantic_critic_enabled = True
    assert reviewer.run(requested).semantic_critic.status == "external_transfer_disabled"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda f, p: f["citations"][0].update(ref_id="E9999"), "unknown_evidence"),
        (
            lambda f, p: f["citations"][0].update(quote="제공하지 않은 법령 문장을 인용했습니다."),
            "quote_mismatch",
        ),
        (lambda f, p: f.update(target_id="unknown-stage"), "unknown_or_mismatched_target"),
        (lambda f, p: f.update(candidate_id="unknown-candidate"), "unknown_or_mismatched_target"),
        (lambda f, p: f.update(target_type="global"), "invalid_global_scope"),
        (lambda f, p: f.update(message="<script>unsafe()</script>"), "unexpected_markup_or_url"),
        (
            lambda f, p: f.update(next_action="https://unprovided.example"),
            "unexpected_markup_or_url",
        ),
    ],
)
def test_hallucinated_citations_and_ids_never_become_map_risks(mutation, reason):
    def transform(output, payload):
        mutation(output["concerns"][0], payload)
        return output

    result = execute(Model(transform))
    assert result.semantic_critic.status == "invalid_output"
    assert result.semantic_critic.rejected_findings[0]["reason"] == reason
    assert not any(i.origin == "model_concern" for i in result.critic)
    assert "semantic_critic_incomplete" in {i.code for i in result.critic}


def test_a_quote_from_another_candidate_is_rejected():
    def transform(output, payload):
        f = output["concerns"][0]
        source = next(
            r for r in payload["evidence"] if r["candidate_id"] not in {None, f["candidate_id"]}
        )
        f["citations"] = [{"ref_id": source["ref_id"], "quote": source["text"][:100]}]
        return output

    result = execute(Model(transform))
    assert result.semantic_critic.rejected_findings[0]["reason"] == "cross_candidate_evidence"


def test_partial_output_preserves_valid_concerns_and_reports_rejection():
    def transform(output, payload):
        second = deepcopy(output["concerns"][0])
        second["citations"][0]["ref_id"] = "E9999"
        output["concerns"].append(second)
        return output

    result = execute(Model(transform))
    assert result.semantic_critic.status == "partial"
    assert (
        len(result.semantic_critic.findings) == len(result.semantic_critic.rejected_findings) == 1
    )


def test_empty_model_output_cannot_erase_a_deterministic_error():
    research, request, bound = setup()
    bound["input_sha256"] = "a" * 64
    model = Model(lambda o, p: {"concerns": []})
    response = (
        DecisionReviewService(research)
        .set_semantic_service(critic(model))
        .run(DecisionReviewRequest(research=request, numeric=bound, semantic_critic={}))
    )
    assert response.route == "REVISE"
    assert response.router["restart_from"] == "kg"
    assert not response.numeric_review["included_candidate_ids"]
    assert response.semantic_critic.status == "completed"
    numeric_records = [
        json.loads(r["text"])
        for r in model.calls[0]["evidence"]
        if r["target_id"] and "numeric_details_withheld" in r["text"]
    ]
    assert len(numeric_records) == 3 and all(r["numeric_details_withheld"] for r in numeric_records)


def test_context_budget_preserves_candidates_and_stops_before_provider():
    model = Model()
    response = execute(model, semantic_critic_max_input_chars=1000)
    assert response.semantic_critic.status == "context_budget_exceeded"
    assert response.semantic_critic.model_calls == 0 and model.calls == []
    assert len(response.candidate_reviews) == 3


def test_actual_meaning_is_not_proven_just_because_a_quote_matches():
    def transform(output, payload):
        output["concerns"][0]["message"] = "가상으로 심은 의미상 오답입니다. 법적 사실이 아닙니다."
        return output

    result = execute(Model(transform))
    assert result.semantic_critic.status == "completed"
    assert result.semantic_critic.findings[0]["semantic_correctness_verified"] is False
    assert all(i.severity == "missing" for i in result.critic if i.origin == "model_concern")
    assert result.route == "REVIEW"


def test_conflicting_content_for_same_evidence_id_blocks_before_model():
    research, request, _ = setup()
    response = DecisionReviewService(research).run(DecisionReviewRequest(research=request))
    for index, search in enumerate(response.research.legal_searches[:2]):
        hit = search.results[0]
        hit.update(version="v1", content=f"가상 원문 충돌 검사 {index}")
    model = Model()
    result = critic(model).run(
        response.research,
        response.numeric_review,
        None,
        [i.model_dump() for i in response.critic],
        SemanticCriticOptions(),
    )
    assert result.status == "evidence_conflict" and not model.calls


@pytest.mark.parametrize(
    "output", [{"concerns": [], "route": "PASS"}, {"concerns": [{"message": "trust me"}]}]
)
def test_model_cannot_supply_router_or_unstructured_decisions(output):
    result = execute(Model(lambda o, p: output))
    assert result.semantic_critic.status == "invalid_output" and result.route == "REVIEW"


def test_provider_failure_is_visible_and_has_no_raw_error_or_retry():
    class Failed(Model):
        def complete(self, task, payload):
            self.calls.append(payload)
            raise RuntimeError("synthetic-private-company-and-key")

    model = Failed()
    result = execute(model)
    assert len(model.calls) == 1 and result.semantic_critic.status == "unavailable"
    assert "synthetic-private" not in result.model_dump_json()


def test_unknown_document_permissions_and_identity_are_excluded_from_payload():
    class Technical(FakeRepository):
        def search_technical(self, q, k):
            return [
                replace(
                    self.search(q, k)[0],
                    content="RESTRICTED-TECHNICAL-CONTENT",
                    document_type="TECHNICAL_LITERATURE",
                )
            ]

    research = CandidateResearchService(FakeGraph(), Technical())
    model = Model()
    request = {
        "lot": {
            "material": "PP",
            "input_form": "scrap",
            "quantity_kg": 3000,
            "contamination": "PRIVATE-COMPANY-NOTE",
        },
        "max_technical_queries": 1,
    }
    result = (
        DecisionReviewService(research)
        .set_semantic_service(critic(model))
        .run(DecisionReviewRequest(research=request, semantic_critic={}))
    )
    encoded = json.dumps(model.calls[0], ensure_ascii=False)
    assert "RESTRICTED-TECHNICAL-CONTENT" not in encoded
    assert "PRIVATE-COMPANY-NOTE" not in encoded
    assert result.semantic_critic.coverage["excluded_source_count"] > 0


def test_site_critic_runs_after_lci_and_never_sends_facility_rows_or_lci_factors():
    model = Model()
    graph = Graph()
    response = SiteReviewService(
        graph, CandidateResearchService(graph, FakeRepository()), [], semantic_service=critic(model)
    ).run(scenario(semantic_critic={}))
    coverage = model.calls[0]["coverage"]["additional_context"]
    assert len(model.calls) == 1
    assert coverage["lci"]["screening_result_count"] == len(response.lci.results)
    assert coverage["lci"]["raw_inventory_or_factors_transferred"] is False
    assert coverage["site"]["registry_rows_transferred"] is False
    encoded = json.dumps(model.calls[0], ensure_ascii=False)
    assert response.site.origin.pickup_address not in encoded
    assert response.site.origin.organisation_id not in encoded


def test_internal_api_requires_critic_token_before_any_model_call():
    research, request, _ = setup()
    model = Model()
    service = critic(model)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = lambda: research
    app.dependency_overrides[get_semantic_critic_service] = lambda: service
    try:
        client = TestClient(app)
        body = DecisionReviewRequest(research=request, semantic_critic={}).model_dump(mode="json")
        assert client.post("/analysis/review", json=body).status_code == 401
        assert (
            client.post(
                "/analysis/review", json=body, headers={"X-Critic-Service-Token": "wrong"}
            ).status_code
            == 401
        )
        assert model.calls == []
        response = client.post(
            "/analysis/review",
            json=body,
            headers={"X-Critic-Service-Token": "synthetic-internal-token"},
        )
        assert response.status_code == 200
        assert response.json()["semantic_critic"]["status"] == "completed"
        assert len(model.calls) == 1
    finally:
        app.dependency_overrides = previous


def test_azure_critic_uses_high_strict_schema_no_storage_or_hosted_tools():
    def handler(request):
        body = json.loads(request.content)
        assert body["reasoning"] == {"effort": "high"} and body["store"] is False
        assert "tools" not in body and body["text"]["format"]["name"] == "semantic_critic"
        payload = json.loads(body["input"])
        output = {"concerns": [concern(payload)]}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "gpt-6-astra",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(output)}],
                    }
                ],
            },
        )

    model = AzureResponsesModel(
        "synthetic-test-key",
        endpoint="https://synthetic.openai.azure.com",
        transport=httpx.MockTransport(handler),
    )
    assert execute(model).semantic_critic.model_identity_verified


@pytest.mark.parametrize(
    "options",
    [
        {"max_findings": 0},
        {"max_findings": 21},
        {"max_findings": True},
        {"allow_external": True},
        {"model": "other-model"},
    ],
)
def test_request_cannot_override_limits_or_server_policy(options):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SemanticCriticOptions.model_validate(options)
