import hashlib
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.api.schemas.legal_graph import LegalGraphRequest
from resource_ai.legal_graph.auditor import Auditor
from resource_ai.legal_graph.builder import build_snapshot, build_structural_snapshot
from resource_ai.legal_graph.llm import BudgetModel, SolarModel
from resource_ai.legal_graph.models import EvidenceSpan, GraphSnapshot, LegalGraphError
from resource_ai.legal_graph.researcher import Researcher, cosine
from resource_ai.legal_graph.service import LegalGraphService
from resource_ai.main import app
from tests.legal_graph_fixtures import MemoryStore, SyntheticModel, documents, enriched


def run(model=None, facts=None):
    snapshot, embeddings, default = enriched()
    model = model or default
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 위탁 조건을 확인", facts or {"permit_confirmed": True}
    )
    return result, snapshot, model


def test_structural_graph_keeps_low_article_numbers_and_no_invented_applies():
    snapshot = build_structural_snapshot(documents(), dataset_sha256="a" * 64)
    assert {n.data["article"] for n in snapshot.nodes if n.kind == "Article"} == {
        "제17조",
        "제25조",
        "제48조",
    }
    assert len([n for n in snapshot.nodes if n.kind == "Case"]) == 3
    assert {e.relation for e in snapshot.edges} == {
        "CITES",
        "CITES_REFERENCE",
        "POSSIBLE_CURRENT_ARTICLE",
    }
    assert all(e.data["version_resolved"] is False for e in snapshot.edges if e.relation == "CITES")
    assert snapshot.metadata["enrichment_complete"] is False


def test_actual_leiden_feature_rule_layers_and_three_retrieval_branches():
    snapshot, embeddings, model = enriched()
    assert snapshot.metadata["community_method"] == "Leiden"
    assert {n.kind for n in snapshot.nodes} >= {
        "Case",
        "Article",
        "Feature",
        "Community",
        "Issue",
        "ChecklistItem",
    }
    result = Researcher(snapshot, embeddings, model).retrieve("가상 위탁", {}, top_k=1)
    assert set(result["strategies"]) == {"semantic", "community", "issue_anchor"}
    assert result["strategies"]["community"]["community_id"]
    union = set().union(*(set(s["case_ids"]) for s in result["strategies"].values()))
    assert set(result["case_ids"]) == union
    assert len(result["article_ids"]) == 3


def test_full_check_decide_prune_adjudicate_does_not_mutate_database():
    result, snapshot, model = run()
    before = snapshot.fingerprint()
    assert result["audit"]["verified_article_ids"]
    assert result["audit"]["verified_case_ids"] == []  # unresolved historic legal version
    assert result["conclusion"]["status"] == "grounded_research_summary"
    tasks = [task for task, _ in model.calls]
    assert tasks.count("audit_item") == tasks.count("audit_article") == 3
    assert tasks.index("audit_item") < tasks.index("audit_article") < tasks.index("adjudicate")
    assert snapshot.fingerprint() == before
    assert result["route"] == "REVIEW" and result["dispatch_approved"] is False
    assert result["audit"]["coverage_complete"] is False  # checklist not expert-validated


@pytest.mark.parametrize("mode", ["unknown", "missing_fact", "foreign", "malformed", "budget"])
def test_missing_invalid_or_failed_evidence_stays_unknown(mode):
    snapshot, embeddings, model = enriched()
    facts, budget = {"permit_confirmed": True}, 40
    if mode == "unknown":
        model.item_status = "unknown"
    elif mode == "missing_fact":
        facts = {}
    elif mode == "foreign":
        model.foreign_evidence = True
    elif mode == "malformed":
        model.malformed_task = "audit_item"
    else:
        budget = 2
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 위탁 확인", facts, max_model_calls=budget
    )
    assert result["audit"]["unknown_article_ids"]
    assert not result["audit"]["inapplicable_article_ids"]
    assert not result["audit"]["verified_article_ids"]
    assert result["conclusion"]["error_code"] == "no-verified-articles"
    assert result["model_calls"] <= budget


def test_false_article_prunes_dependent_case_only_in_current_result():
    snapshot, embeddings, model = enriched()
    before = snapshot.fingerprint()
    research = Researcher(snapshot, embeddings, model).retrieve("가상 위탁", {})
    model.item_status, model.article_status = "not_met", "inapplicable"
    result = Auditor(snapshot, model).audit(research, {"permit_confirmed": False})
    assert len(result["inapplicable_article_ids"]) == 3
    assert len(result["pruned_case_ids"]) == 3
    assert not result["retained_case_ids"]
    assert snapshot.fingerprint() == before


def test_invented_quote_fails_graph_construction():
    from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider

    model = SyntheticModel()
    model.false_quote = True
    with pytest.raises(LegalGraphError, match="인용"):
        build_snapshot(documents(), HashEmbeddingProvider(), model, dataset_sha256="a" * 64)


def test_missing_checklists_and_tampered_offsets_do_not_pass():
    snapshot, embeddings, model = enriched()
    item = next(n for n in snapshot.nodes if n.kind == "ChecklistItem")
    item.data["start"] += 1
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 위탁", {"permit_confirmed": True}
    )
    assert any(r["status"] == "unknown" for r in result["audit"]["articles"])
    assert any(
        i["error_code"] == "checklist-grounding"
        for r in result["audit"]["articles"]
        for i in r["items"]
    )


def test_unknown_or_rejected_article_cannot_appear_in_final_findings():
    model = SyntheticModel()
    model.forbidden_final = True
    result, _, _ = run(model)
    assert result["conclusion"]["status"] == "review_required"
    assert result["conclusion"]["error_code"] == "model-grounding"
    assert not result["conclusion"]["findings"]


def test_future_statute_and_future_case_never_reach_auditor():
    snapshot, embeddings, model = enriched()
    future = next(n for n in snapshot.nodes if n.kind == "Article")
    for eid in future.evidence_ids:
        snapshot.evidence_map()[eid].effective_from = date(2030, 1, 1)
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 위탁", {"as_of": "2025-01-01", "permit_confirmed": True}
    )
    assert future.id in result["research"]["future_or_noncurrent_article_ids"]
    assert future.id not in result["audit"]["verified_article_ids"]
    assert all(
        payload.get("article_id") != future.id
        for task, payload in model.calls
        if task.startswith("audit_")
    )


def test_structure_only_cannot_masquerade_as_hierarchical_retrieval():
    snapshot, embeddings, model = enriched()
    snapshot.metadata["enrichment_complete"] = False
    with pytest.raises(LegalGraphError) as exc:
        Researcher(snapshot, embeddings, model).retrieve("가상 위탁", {})
    assert exc.value.code == "graph-not-enriched"


@pytest.mark.parametrize("a,b", [([], []), ([1], [1, 2]), ([0], [0]), ([float("nan")], [1])])
def test_bad_embedding_fails_closed(a, b):
    with pytest.raises(LegalGraphError):
        cosine(a, b)


def test_exact_source_bytes_and_digest_survive_validation():
    text = "  원문\n"
    span = EvidenceSpan(
        id="e",
        source_id="s",
        source_url="https://www.law.go.kr",
        title="t",
        section_key="0",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    assert span.text == text
    with pytest.raises(ValidationError):
        EvidenceSpan.model_validate({**span.model_dump(), "text": "조작"})
    snapshot, _, _ = enriched()
    snapshot.edges[0].target = "missing"
    with pytest.raises(ValidationError):
        GraphSnapshot.model_validate(snapshot.model_dump())


def test_strict_provider_json_and_error_redaction():
    def response(request):
        assert request.headers["authorization"] == "Bearer test-not-a-real-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"summary":"가상 요약"}'}}
                ]
            },
        )

    provider = SolarModel("test-not-a-real-key", transport=httpx.MockTransport(response))
    assert provider.complete("summarize_community", {}) == {"summary": "가상 요약"}
    bad = SolarModel(
        "test-not-a-real-key",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(500, text="PRIVATE FACTS AND TOKEN")
        ),
    )
    with pytest.raises(LegalGraphError) as exc:
        bad.complete("summarize_community", {})
    assert "PRIVATE" not in str(exc.value) and "TOKEN" not in str(exc.value)


def test_model_budget_never_silently_calls_again():
    model = BudgetModel(SyntheticModel(), 1)
    model.complete("summarize_community", {})
    with pytest.raises(LegalGraphError) as exc:
        model.complete("summarize_community", {})
    assert exc.value.code == "model-budget"
    assert model.calls == 1


def test_new_api_runs_injected_model_and_defaults_to_explicit_unconfigured(monkeypatch):
    import resource_ai.api.routes.legal_graph as routes

    snapshot, embeddings, model = enriched()
    monkeypatch.setattr(
        routes,
        "get_legal_service",
        lambda: LegalGraphService(MemoryStore(snapshot), embeddings, model),
    )
    response = TestClient(app).post(
        "/legal-graph/analyze", json={"query": "가상 위탁", "facts": {"permit_confirmed": True}}
    )
    assert response.status_code == 200
    assert response.json()["model"] == SyntheticModel.name

    def unavailable():
        raise LegalGraphError("model-not-configured", "모델 미연결")

    monkeypatch.setattr(routes, "get_legal_service", unavailable)
    response = TestClient(app).post("/legal-graph/analyze", json={"query": "가상 위탁"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model-not-configured"


def test_api_rejects_oversized_facts_and_internal_hypotheses():
    with pytest.raises(ValidationError):
        LegalGraphRequest(query="가상 검토", facts={"x": "a" * 33000})
    with pytest.raises(ValidationError):
        LegalGraphRequest(query="가상 검토", facts={"proposed_route": "승인됨"})


def test_explicit_interpretation_document_links_to_exact_source_version():
    from resource_ai.evidence.models import LegalDocument, LegalSection
    from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider

    interpretation = LegalDocument(
        source_id="synthetic-interpretation-v1",
        document_type="INTERPRETATION",
        title="가상 공식 해석",
        source_url="https://www.law.go.kr/example-test-only",
        version="synthetic-v1",
        effective_from=date(2020, 1, 1),
        metadata={
            "version_verified": True,
            "article_references": [{"source_id": "synthetic-law-v1", "article": "제17조"}],
        },
        sections=[
            LegalSection(heading="가상 해석", content="가상 허가 조건 해석", citation="가상 해석")
        ],
    )
    embeddings, model = HashEmbeddingProvider(32), SyntheticModel()
    snapshot = build_snapshot(
        [*documents(), interpretation], embeddings, model, dataset_sha256="a" * 64
    )
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 검토", {"permit_confirmed": True}
    )
    assert any(r["interpretation_ids"] for r in result["audit"]["articles"])
    assert any(p["interpretations"] for task, p in model.calls if task == "audit_item")
    interpretation.metadata["article_references"][0]["source_id"] = "another-version"
    with pytest.raises(LegalGraphError) as exc:
        build_structural_snapshot([*documents(), interpretation], dataset_sha256="a" * 64)
    assert exc.value.code == "unresolved-interpretation"


def test_future_required_interpretation_stays_unknown_and_is_not_sent():
    snapshot, embeddings, model = enriched()
    article = next(n for n in snapshot.nodes if n.kind == "Article")
    article.data["requires_interpretation"] = True
    result = LegalGraphService(MemoryStore(snapshot), embeddings, model).run(
        "가상 검토", {"permit_confirmed": True}
    )
    record = next(r for r in result["audit"]["articles"] if r["article_id"] == article.id)
    assert record["status"] == "unknown" and record["error_code"] == "missing-interpretation"


def test_decide_can_handle_alternative_conditions_instead_of_simple_and():
    from resource_ai.legal_graph.models import LegalEdge

    snapshot, embeddings, _ = enriched()
    original = next(n for n in snapshot.nodes if n.kind == "ChecklistItem")
    article_id = next(
        e.source
        for e in snapshot.edges
        if e.target == original.id and e.relation == "HAS_CHECKLIST"
    )
    alternative = original.model_copy(
        deep=True, update={"id": "synthetic-alternative-item", "text": "다른 허용 조건인가?"}
    )
    snapshot.nodes.append(alternative)
    snapshot.edges.append(
        LegalEdge(source=article_id, target=alternative.id, relation="HAS_CHECKLIST")
    )

    class AlternativeModel(SyntheticModel):
        def complete(self, task, payload):
            result = super().complete(task, payload)
            if task == "audit_item" and payload["item"]["id"] == original.id:
                result["status"] = "not_met"
            if task == "audit_article" and payload["article_id"] == article_id:
                result["decisive_item_ids"] = [alternative.id]
            return result

    result = LegalGraphService(MemoryStore(snapshot), embeddings, AlternativeModel()).run(
        "가상 대안 조건", {"permit_confirmed": True}
    )
    record = next(r for r in result["audit"]["articles"] if r["article_id"] == article_id)
    assert {i["status"] for i in record["items"]} == {"met", "not_met"}
    assert record["status"] == "applicable"


def test_pruning_preserves_case_that_still_supports_another_verified_article():
    from resource_ai.legal_graph.models import LegalEdge

    snapshot, embeddings, model = enriched()
    research = Researcher(snapshot, embeddings, model).retrieve("가상 검토", {})
    rejected = research["article_ids"][0]
    other = research["article_ids"][1]
    case_id = next(
        e.source for e in snapshot.edges if e.relation == "CITES" and e.target == rejected
    )
    snapshot.edges.append(
        LegalEdge(source=case_id, target=other, relation="CITES", data={"version_resolved": True})
    )

    class MixedModel(SyntheticModel):
        def complete(self, task, payload):
            result = super().complete(task, payload)
            if task == "audit_article" and payload["article_id"] == rejected:
                result["status"] = "inapplicable"
            return result

    result = Auditor(snapshot, MixedModel()).audit(research, {"permit_confirmed": True})
    assert rejected in result["inapplicable_article_ids"]
    assert case_id in result["retained_case_ids"] and case_id in result["verified_case_ids"]
    assert case_id not in result["pruned_case_ids"]


def test_embedding_model_identity_and_pinned_generation():
    from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider

    snapshot, embeddings, model = enriched()
    changed = HashEmbeddingProvider(32)
    changed.model_name = "different-model-same-dimension"
    with pytest.raises(LegalGraphError) as exc:
        Researcher(snapshot, changed, model).retrieve("가상 검토", {})
    assert exc.value.code == "embedding-integrity"
    store = MemoryStore(snapshot)
    pinned = LegalGraphService(store, embeddings, model).pin()
    store.snapshot = build_structural_snapshot(documents(), dataset_sha256="a" * 64)
    result = pinned.run("가상 검토", {"permit_confirmed": True})
    assert result["graph_trace"]["fingerprint"] == snapshot.fingerprint()


@pytest.mark.parametrize(
    "content,finish",
    [
        ("not json", "stop"),
        ('{"summary":"partial"}', "length"),
        ('{"summary":"x","extra":1}', "stop"),
    ],
)
def test_provider_rejects_malformed_truncated_or_extra_fields(content, finish):
    provider = SolarModel(
        "test-not-a-real-key",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
            )
        ),
    )
    with pytest.raises(LegalGraphError):
        provider.complete("summarize_community", {})


def test_multiple_law_versions_are_filtered_at_query_date_without_losing_old_version():
    from dataclasses import replace

    from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider

    docs = documents()
    newer = replace(
        docs[0],
        source_id="synthetic-law-v2",
        version="synthetic-v2",
        effective_from=date(2024, 1, 1),
    )
    embeddings, model = HashEmbeddingProvider(32), SyntheticModel()
    snapshot = build_snapshot([*docs, newer], embeddings, model, dataset_sha256="a" * 64)
    for as_of, source in [("2023-01-01", "synthetic-law-v1"), ("2025-01-01", "synthetic-law-v2")]:
        result = Researcher(snapshot, embeddings, model).retrieve("가상 검토", {"as_of": as_of})
        assert len(result["article_ids"]) == 3
        assert {snapshot.node_map()[aid].data["source_id"] for aid in result["article_ids"]} == {
            source
        }


def test_unavailable_law_reference_does_not_inherit_previous_law_name():
    from dataclasses import replace

    docs = documents()
    case = docs[1]
    section = replace(case.sections[1], content="가상 폐자원법 제17조, 미수집법 제25조")
    docs[1] = replace(case, sections=[case.sections[0], section])
    snapshot = build_structural_snapshot(docs, dataset_sha256="a" * 64)
    case_node = next(
        n for n in snapshot.nodes if n.kind == "Case" and n.data["source_id"] == case.source_id
    )
    targets = [
        snapshot.node_map()[e.target].data["article"]
        for e in snapshot.edges
        if e.source == case_node.id and e.relation == "CITES"
    ]
    assert targets == ["제17조"]


def test_cli_invalid_private_request_does_not_echo_request(tmp_path, monkeypatch, capsys):
    from resource_ai.legal_graph import cli

    request = tmp_path / "private-request.json"
    request.write_text('{"query":"PRIVATE-SYNTHETIC-FACT","unsupported":true}', encoding="utf-8")
    monkeypatch.setattr(cli, "LegalGraphStore", lambda **kwargs: None)
    assert cli.main(["analyze", "--request", str(request)]) == 1
    output = capsys.readouterr().out
    assert "invalid-input" in output
    assert "PRIVATE-SYNTHETIC-FACT" not in output
