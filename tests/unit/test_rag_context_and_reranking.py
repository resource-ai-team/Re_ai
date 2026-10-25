from dataclasses import replace
from pathlib import Path

import pytest

from resource_ai.rag.legal_context import expand_context, references
from resource_ai.rag.retrieval.cross_encoder import OnnxCrossEncoder
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.service import EvidenceRepository
from scripts.build_rag_study_cases import build
from tests.unit.test_rag_service import SearchDatabase
from tests.unit.test_retriever import _result


def test_explicit_statute_decree_and_annex_links_are_bounded_by_owner():
    found, unresolved = references(
        "법 제25조, 영 제7조, 「순환경제사회 전환 촉진법」 제21조와 별표 5의3",
        "폐기물관리법 시행규칙",
    )
    assert found == sorted(
        [
            ("폐기물관리법", "제25조"),
            ("폐기물관리법 시행령", "제7조"),
            ("순환경제사회 전환 촉진법", "제21조"),
            ("폐기물관리법 시행규칙", "별표5의3"),
        ]
    )
    assert not unresolved


def test_bare_or_historical_reference_does_not_silently_bind_to_current_law():
    found, unresolved = references(
        "구 「폐기물관리법」 제25조 및 제29조, 다른법 제2조", "폐기물관리법"
    )
    assert found == []
    assert len(unresolved) == 3


def test_unspecified_delegation_is_not_silently_marked_as_collected():
    found, unresolved = references("이영 제7조 및 대통령령으로 정하는 기준", "폐기물관리법 시행령")
    assert found == [("폐기물관리법 시행령", "제7조")]
    assert unresolved == ["delegated_instrument_locator_unknown:대통령령"]


@pytest.mark.parametrize(
    "kwargs", [{"max_links": 17}, {"max_roots": 0}, {"max_chars": 120001}, {"max_chunks": 65}]
)
def test_context_rejects_unbounded_work_before_database(kwargs):
    with pytest.raises(ValueError):
        expand_context(None, [], **kwargs)


def test_candidate_minimum_is_independent_of_final_top_k_and_capped():
    database = SearchDatabase()
    repo = EvidenceRepository(database, HashEmbeddingProvider(), candidate_minimum=100)
    assert repo.search("폐기물처리업 허가", 3, search_group="LEGAL") == []
    assert all(params[-1] == 100 for _, params in database.calls)
    assert all(
        "d.tenant_id IS NULL OR d.document_type = 'COMPANY_DOCUMENT'" in query
        for query, _ in database.calls
    )


def test_cross_encoder_keeps_hybrid_score_and_group_boundary():
    ranker = object.__new__(OnnxCrossEncoder)
    ranker.model_id = "synthetic-model@version"
    ranker.scores = lambda query, rows: [0.1, 0.9]
    first, second = _result(1, "제25조"), _result(2, "제29조")
    private = replace(_result(3, "성적서"), document_type="COMPANY_DOCUMENT")
    ranked = ranker.rerank("허가", [first, second, private], limit=1, search_group="LEGAL")
    assert ranked[0].chunk_id == 2 and ranked[0].score == second.score
    assert ranked[0].reranker_score == 0.9
    assert ranked[0].reranker_model == "synthetic-model@version"


def test_model_failure_cannot_silently_become_heuristic_success():
    class Broken:
        def rerank(self, *args, **kwargs):
            raise RuntimeError("synthetic model outage")

    repo = EvidenceRepository(SearchDatabase(), HashEmbeddingProvider(), reranker=Broken())
    with pytest.raises(RuntimeError, match="outage"):
        repo.search("허가")


def test_neural_input_and_file_budgets():
    ranker = object.__new__(OnnxCrossEncoder)
    with pytest.raises(ValueError, match="budget"):
        ranker.scores("query", [_result(i, "조문") for i in range(101)])
    with pytest.raises(ValueError, match="configuration"):
        OnnxCrossEncoder(Path("."), "missing.onnx", model_id="test", max_length=9999)


def test_development_and_test_families_do_not_overlap():
    data = build()
    assert len(data["cases"]) == 50
    dev = {r["family_id"] for r in data["cases"] if r["split"] == "dev"}
    test = {r["family_id"] for r in data["cases"] if r["split"] == "test"}
    assert dev.isdisjoint(test)
    assert sum(r["split"] == "dev" for r in data["cases"]) == 27
    assert "expert_review_pending" in data["label_status"]


def test_legal_query_does_not_use_enterprise_identity_as_a_legal_search_term():
    from resource_ai.agents.research import legal_questions

    stage = {"transition": {"label": "세척"}, "facility": {"label": "가상A비밀공장"}}
    queries = legal_questions("PET", stage)
    assert all("가상A비밀공장" not in query for _, query in queries)
    assert "허가" in dict(queries)["facility"]


def test_distinct_articles_preserves_order_and_does_not_merge_article_suffixes():
    from resource_ai.rag.retrieval.diversity import distinct_articles

    first = replace(_result(1, "first"), citation="폐기물관리법 제25조 ①항")
    second = replace(_result(2, "second"), citation="폐기물관리법 제25조 ②항")
    third = replace(_result(3, "third"), citation="폐기물관리법 제25조의3 ①항")
    assert [h.chunk_id for h in distinct_articles([first, second, third], 3)] == [1, 3]


def test_context_provider_failure_does_not_remove_retrieved_hit_or_hide_gap():
    from resource_ai.agents.research import retrieve
    from tests.unit.test_candidate_research import FakeRepository

    class Repo(FakeRepository):
        def expand_legal_context(self, hits):
            raise RuntimeError("synthetic provider outage")

    status, hits = retrieve(Repo(), "폐기물처리업", 3)
    assert status == "retrieved"
    assert hits[0]["legal_context"]["status"] == "unavailable"
    assert hits[0]["applicability"] == "not_audited"


def test_context_stale_seed_and_mixed_versions_become_critic_errors():
    from resource_ai.agents.decision_critic import inspect_review
    from resource_ai.agents.research import CandidateResearchService
    from resource_ai.api.schemas.analysis import ResearchRequest
    from tests.unit.test_candidate_research import FakeGraph, FakeRepository

    response = CandidateResearchService(FakeGraph(), FakeRepository()).run(
        ResearchRequest(lot={"material": "PP"})
    )
    hit = response.legal_searches[0].results[0]
    hit["version"] = "v1"
    hit["legal_context"] = {
        "status": "review_required",
        "source_checks": ["stale_or_unresolvable_seed"],
        "primary": [{"title": hit["title"], "version": "v2"}],
        "linked": [],
    }
    codes = {r["code"] for r in inspect_review(response, None, None) if r["severity"] == "error"}
    assert {"legal_context_stale", "legal_source_version_changed"} <= codes
