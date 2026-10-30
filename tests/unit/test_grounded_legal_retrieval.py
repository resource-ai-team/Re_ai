from dataclasses import replace
from types import SimpleNamespace

import pytest

from resource_ai.evidence.models import EvidenceSection
from resource_ai.rag.legal_aliases import extract_aliases
from resource_ai.rag.legal_context import references
from resource_ai.rag.retrieval.diversity import distinct_articles
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.korean_lexical import KoreanLegalTokenizer
from resource_ai.rag.service import EvidenceRepository
from tests.unit.test_rag_service import SearchDatabase
from tests.unit.test_retriever import _result


def test_unique_definition_keeps_exact_quote_and_citation():
    quote = '「폐기물관리법」(이하 "법"이라 한다)'
    aliases, blocked = extract_aliases(
        [EvidenceSection("목적", quote + " 제25조에 따른다.", "허가 지침 I")]
    )
    assert not blocked
    assert aliases["법"] == {
        "title": "폐기물관리법",
        "definition": quote,
        "citation": "허가 지침 I",
    }
    found, unresolved = references(
        quote + " 제25조 및 법 제25조의3",
        "허가 지침",
        administrative_rule=True,
        aliases={"법": "폐기물관리법"},
    )
    assert found == [("폐기물관리법", "제25조"), ("폐기물관리법", "제25조의3")]
    assert not unresolved


@pytest.mark.parametrize(
    "second",
    [
        '「물환경보전법」(이하 "법"이라 한다)',
        '「폐기물관리법」(이하 이 조에서 "법"이라 한다)',
        '구 「폐기물관리법」(이하 "법"이라 한다)',
    ],
)
def test_conflicting_scoped_or_historical_alias_is_not_global(second):
    aliases, blocked = extract_aliases(
        [EvidenceSection("목적", '「폐기물관리법」(이하 "법"이라 한다)\n' + second, "preamble")]
    )
    assert "법" not in aliases and "법" in blocked


def test_missing_definition_does_not_resolve_upper_law_by_guideline_title():
    refs, unknown = references("법 제25조", "폐기물 처리 허가 지침", administrative_rule=True)
    assert refs == [] and unknown[0].startswith("unresolved_administrative_alias:")


def test_article_annex_and_freeform_chapter_are_deduplicated_separately():
    base = replace(
        _result(1, "test"),
        document_type="LAW",
        title="폐기물관리법",
        version="v1",
        citation="폐기물관리법 제25조 ①항",
        heading="제25조",
    )
    same = replace(base, chunk_id=2, citation="폐기물관리법 제25조 ②항")
    branch = replace(base, chunk_id=3, citation="폐기물관리법 제25조의3", heading="제25조의3")
    annex = replace(base, chunk_id=4, citation="폐기물관리법 별표 1", heading="별표 1")
    annex2 = replace(annex, chunk_id=5)
    chapter = replace(base, chunk_id=6, citation="허가 지침 III", heading="III 허가")
    chapter2 = replace(chapter, chunk_id=7)
    assert [
        h.chunk_id
        for h in distinct_articles([base, same, branch, annex, annex2, chapter, chapter2], 10)
    ] == [1, 3, 4, 6]


def test_tokenizer_drops_particles_and_operators_without_caching_business_queries():
    tokenizer = object.__new__(KoreanLegalTokenizer)
    tokens = [
        SimpleNamespace(form=f, tag=t, start=s, len=n)
        for f, t, s, n in [
            ("폐기물", "NNG", 0, 3),
            ("처리", "NNG", 4, 2),
            ("업체", "NNG", 6, 2),
            ("의", "JKG", 8, 1),
            ("허가", "NNG", 10, 2),
            ("DROP|TABLE", "SL", 13, 10),
        ]
    ]
    tokenizer.kiwi = SimpleNamespace(tokenize=lambda _: tokens)
    assert tokenizer.terms("any") == ("폐기물", "처리", "업체", "처리업체", "허가")
    assert tokenizer.query("any") == "폐기물 | 처리 | 업체 | 처리업체 | 허가"
    assert not hasattr(tokenizer.terms, "cache_info")
    with pytest.raises(ValueError, match="budget"):
        tokenizer.terms("가" * 12001)


def test_korean_lane_never_widens_corpus_or_tenant_scope():
    tokenizer = SimpleNamespace(recipe="test", query=lambda _: "허가")
    database = SearchDatabase()
    repository = EvidenceRepository(database, HashEmbeddingProvider(), korean_tokenizer=tokenizer)
    repository.search("허가", corpus_types=["LAW"])
    assert len(database.calls) == 3
    sql, params = database.calls[-1]
    assert "d.tenant_id IS NULL" in sql and ["LAW"] in params
    assert "d.effective_from<=CURRENT_DATE" in sql
    company = SearchDatabase()
    EvidenceRepository(company, HashEmbeddingProvider(), korean_tokenizer=tokenizer).search(
        "기업 성적서", search_group="COMPANY", tenant_id="tenant-a"
    )
    assert len(company.calls) == 2


@pytest.mark.parametrize("types", [None, [], ["COMPANY_DOCUMENT"], ["LAW", "TECHNICAL_LITERATURE"]])
def test_bm25_requires_explicit_public_legal_scope(types):
    from resource_ai.rag.retrieval.korean_lexical import lexical_search

    with pytest.raises(ValueError, match="explicit public legal"):
        lexical_search(None, "query", None, 5, types)


@pytest.mark.parametrize("limit", [True, 0, -1, 101, 1.2])
def test_bm25_candidate_budget_is_bounded(limit):
    from resource_ai.rag.retrieval.korean_lexical import lexical_search

    with pytest.raises(ValueError, match="integer between"):
        lexical_search(None, "query", None, limit, ["LAW"])


def test_article_fusion_does_not_reward_many_chunks_or_miss_cross_paragraph_support():
    from resource_ai.rag.retrieval.diversity import article_identity
    from resource_ai.rag.retrieval.retriever import reciprocal_rank_fusion

    base = replace(
        _result(1, "example"),
        document_type="LAW",
        title="가상법",
        version="v1",
        citation="가상법 제25조 ①항",
        metadata={"original": True},
    )
    second = replace(base, chunk_id=2, citation="가상법 제25조 ②항")
    branch = replace(base, chunk_id=3, citation="가상법 제25조의3")
    ranked = reciprocal_rank_fusion(
        [base, second, branch], [second, second], limit=5, identity=article_identity
    )
    assert [h.chunk_id for h in ranked] == [1, 3]
    assert ranked[0].score == pytest.approx(2 / 61)
    assert ranked[0].metadata == {"original": True}
    assert ranked[1].score == pytest.approx(1 / 62)
    assert article_identity(replace(base, version="v2")) != article_identity(base)


def test_reingest_cannot_claim_unbuilt_lexical_recipe_from_section_metadata():
    from tests.unit.test_rag_service import UnusedDatabase, _law

    doc = _law()
    doc = replace(
        doc,
        sections=[
            replace(
                doc.sections[0],
                metadata={"korean_lexical_recipe": "claimed", "_korean_lexical_inputs": ("x",) * 3},
            )
        ],
    )
    _, chunks, _ = EvidenceRepository(UnusedDatabase(), HashEmbeddingProvider())._prepare_document(
        doc
    )
    assert "korean_lexical_recipe" not in chunks[0][4]
    assert "_korean_lexical_inputs" not in chunks[0][4]


def test_basis_balancing_preserves_exact_requested_document_and_limit():
    from resource_ai.rag.retrieval.legal_basis import balance_basis, purpose_text

    guide = replace(_result(1, "지침"), title="순환자원 인정 절차 고시", citation="인정 고시 제4조")
    basis = replace(
        _result(2, "법령"), title="가상법", citation="가상법 제21조", evidence_role="cited_basis"
    )
    assert balance_basis([guide], [basis], 1, query="순환자원 인정 절차 고시 제4조")[0] == guide
    assert balance_basis([guide], [basis], 1, query="제도의 법적 근거")[0].chunk_id == 2
    assert (
        purpose_text("목 적\n법 제25조 허가\n2. 적용\n다른 법 제99조") == "목 적\n법 제25조 허가\n"
    )
    assert purpose_text("참고 사항: 다른 법 제99조") == ""


def test_nonstandard_rule_does_not_invent_a_parent_law_name():
    refs, unknown = references("법 제17조", "산업안전보건기준에 관한 규칙")
    assert refs == [] and unknown[0].startswith("unresolved_legal_alias:")


def test_conflicting_definition_cannot_fall_back_to_a_guessed_parent_statute():
    refs, unknown = references(
        "법 제17조 및 「폐기물관리법」 제25조",
        "폐기물관리법 시행규칙",
        aliases={"법": "폐기물관리법"},
        blocked_aliases=["법"],
    )
    assert refs == [("폐기물관리법", "제25조")]
    assert unknown[0].startswith("conflicting_legal_alias:")
    refs, unknown = references("규칙 제2조", "폐기물관리법 시행령")
    assert refs == [] and unknown[0].startswith("unresolved_legal_alias:")


def test_basis_requires_official_public_source_urls():
    from resource_ai.rag.retrieval.legal_basis import official_url

    assert official_url("https://www.law.go.kr/법령/폐기물관리법")
    assert not official_url("https://user:secret@www.law.go.kr/법령/폐기물관리법")
    assert not official_url("https://example.org/fake-law")
    assert not official_url("http://www.law.go.kr/법령/폐기물관리법")


def test_critic_budget_keeps_facts_and_complete_supplied_groups_marks_omissions():
    import json

    from resource_ai.agents.semantic_critic import SemanticCriticService
    from resource_ai.agents.semantic_packet import PacketError, select_evidence

    def record(id_, kind, text, citation=None):
        return {
            "ref_id": id_,
            "kind": kind,
            "text": text,
            "evidence_id": "semantic-coverage" if id_ == "E5" else f"legal-chunk:{id_}",
            "candidate_id": None,
            "target_id": None,
            "metadata": {
                "title": "가상법",
                "version": "v1",
                "source_url": "https://www.law.go.kr/",
                "citation": citation,
            }
            if citation
            else {},
        }

    records = {
        "E1": record("E1", "engine_record", "fixed candidate and numeric facts"),
        "E2": record("E2", "official_statute", "긴 법령 " * 2000, "가상법 제25조 ①항"),
        "E3": record("E3", "official_statute", "다만, 예외 " * 2000, "가상법 제25조 ②항"),
        "E4": record("E4", "official_statute", "배출자 의무 근거", "가상법 제17조"),
        "E5": record("E5", "engine_record", "coverage"),
    }
    payload = {"coverage": {}, "allowed_targets": {}, "evidence": []}
    packet, kept, coverage = select_evidence(payload, records, {"E2": 2, "E3": 2, "E4": 0}, 3000)
    assert len(json.dumps(packet, ensure_ascii=False)) <= 3000
    assert set(kept) == {"E1", "E4", "E5"}
    assert coverage["omitted_legal_evidence_count"] == 2
    assert not coverage["all_legal_contexts_included"]
    assert not coverage["legal_document_completeness_guaranteed"]
    assert json.loads(kept["E5"]["text"]) == coverage
    assert "legal-chunk:" not in json.dumps(packet, ensure_ascii=False)
    concern = SimpleNamespace(
        message="검토 필요",
        next_action="근거 확인",
        target_type="global",
        candidate_id=None,
        target_id=None,
        citations=[SimpleNamespace(ref_id="E2", quote="긴 법령")],
    )
    assert SemanticCriticService._check(concern, kept, {}) == "unknown_evidence"
    with pytest.raises(PacketError, match="context_budget_exceeded"):
        select_evidence(payload, records, {}, 10)
