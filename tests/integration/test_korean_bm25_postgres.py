"""Real SQL with synthetic records in a disposable, isolated schema."""

import os
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.korean_lexical import RECIPE, index_coverage, lexical_search
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


class IndexedTerms:
    """SQL fixtures use explicit lexemes; the real Kiwi tokenizer has separate tests."""

    recipe = RECIPE

    @staticmethod
    def text(value):
        return " ".join(dict.fromkeys(re.findall(r"[가-힣a-z]+", value.lower())))

    def query(self, value):
        return " | ".join(self.text(value).split()[:32])


@pytest.fixture
def repository():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    url = get_settings().database_url
    schema = "re_ai_bm25_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = EvidenceRepository(
            Database(make_conninfo(url, options=f"-c search_path={schema},public")),
            HashEmbeddingProvider(),
            korean_tokenizer=IndexedTerms(),
        )
        repo.initialize(Path("migrations"))
        yield repo
    finally:
        assert schema.startswith("re_ai_bm25_test_") and len(schema) == 48
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def document(id_, content, **kwargs):
    return EvidenceDocument(
        id_,
        "LAW",
        "가상 운반 기준 " + id_,
        "https://www.law.go.kr/법령/가상기준",
        [
            EvidenceSection(
                "제1조 운반 의무",
                content,
                "가상기준 제1조",
                metadata={"article": "제1조", "article_title": "운반 의무"},
            )
        ],
        effective_from=date(2020, 1, 1),
        version="test-v1",
        metadata={"legal_source_kind": "statute"},
        **kwargs,
    )


def search(repo, query="운반 인계번호 숙지", types=("LAW",)):
    with repo.database.connect() as c:
        return repo._results(lexical_search(c, query, repo.korean_tokenizer, 100, types), "bm25")


def test_rare_operative_word_beats_repeated_generic_guide_and_keeps_provenance(repository):
    exact = document("operative", "운반 인계번호 숙지 의무")
    repository.ingest_many(
        [exact, document("generic", "운반 인계번호 의무 안내 " * 250)]
        + [document(f"background-{i}", "운반 인계번호 처리 안내") for i in range(6)]
    )
    hits = search(repository)
    assert hits[0].title.endswith("operative")
    assert hits[0].content == exact.sections[0].content
    assert hits[0].metadata["document"]["legal_source_kind"] == "statute"
    assert hits[0].metadata["chunk"]["article"] == "제1조"
    assert all(h.score > 0 for h in hits)


def test_scope_statistics_update_and_reingest_do_not_reuse_stale_terms(repository):
    public = document("public", "운반 인계번호 숙지 의무")
    repository.ingest(public)
    before = search(repository)[0]
    repository.ingest_many(
        [
            document("private", "운반 인계번호 숙지 의무", tenant_id="tenant-secret"),
            replace(document("future", "운반 인계번호 숙지"), effective_from=date(2099, 1, 1)),
            document("inactive", "운반 인계번호 숙지"),
            document("wrong-recipe", "운반 인계번호 숙지"),
        ]
    )
    with repository.database.connect() as c:
        # Simulate stale/badly populated vectors too; SQL scope must still exclude them.
        c.execute(
            """UPDATE legal_chunks ch SET korean_search_vector=
            (SELECT korean_search_vector FROM legal_chunks
             WHERE document_id=(SELECT id FROM legal_documents WHERE source_id='public')),
            metadata=ch.metadata || jsonb_build_object('korean_lexical_recipe',%s::text)
            WHERE document_id=(SELECT id FROM legal_documents WHERE source_id='private')""",
            (RECIPE,),
        )
        c.execute("UPDATE legal_documents SET is_current=false WHERE source_id='inactive'")
        c.execute("""UPDATE legal_chunks SET metadata=metadata ||
            '{"korean_lexical_recipe":"obsolete"}'::jsonb
            WHERE document_id=(SELECT id FROM legal_documents WHERE source_id='wrong-recipe')""")
    after = search(repository)
    assert [h.chunk_id for h in after] == [before.chunk_id]
    assert after[0].score == pytest.approx(before.score)  # Excluded records cannot change IDF.
    rare_before = search(repository, "숙지")[0].score
    repository.ingest(document("new-current", "인계번호 처리"))
    assert search(repository, "숙지")[0].score != pytest.approx(rare_before)
    repository.ingest(replace(public, sections=[EvidenceSection("개정", "운반 처리", "제1조")]))
    assert search(repository, "숙지") == []


def test_precedent_scope_and_empty_or_unindexed_corpus(repository):
    assert search(repository) == []
    with repository.database.connect() as c:
        assert not index_coverage(c)["complete"]
    repository.ingest(document("law", "운반 인계번호 숙지"))
    repository.ingest(
        replace(
            document("case", "운반 인계번호 숙지 판결"),
            document_type="PRECEDENT",
            effective_from=None,
        )
    )
    assert [h.document_type for h in search(repository, types=("PRECEDENT",))] == ["PRECEDENT"]
    assert {h.document_type for h in search(repository, types=("LAW", "PRECEDENT"))} == {
        "LAW",
        "PRECEDENT",
    }
    with repository.database.connect() as c:
        assert index_coverage(c)["complete"]
    repository.korean_tokenizer = None
    repository.ingest(document("unindexed", "아직없는단어"))
    repository.korean_tokenizer = IndexedTerms()
    assert search(repository, "아직없는단어") == []
    with repository.database.connect() as c:
        coverage = index_coverage(c)
        assert not coverage["complete"] and coverage["missing_chunks"] == 1


def test_administrative_section_literal_basis_is_followed_without_a_purpose_article_reference(
    repository,
):
    from pgvector import Vector

    from resource_ai.rag.retrieval.legal_basis import cited_basis

    law = replace(document("basis", "운반 숙지 의무"), title="가상폐기물법")
    guide = replace(
        document("guide", "unused"),
        title="가상 업무 지침",
        source_url="https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=123",
        metadata={"legal_source_kind": "administrative_rule"},
        sections=[
            EvidenceSection(
                "제1조 목적",
                '제1조(목적) 「가상폐기물법」(이하 "법"이라 한다)의 절차를 설명한다.',
                "가상 업무 지침 제1조",
                metadata={"article": "제1조", "article_title": "목적"},
            ),
            EvidenceSection(
                "제2조 운반",
                "법 제1조에 따라 운반 의무를 확인한다.",
                "가상 업무 지침 제2조",
                metadata={"article": "제2조", "article_title": "운반"},
            ),
        ],
    )
    rule = replace(law, source_id="basis-rule", title="가상폐기물법 시행규칙")
    guide = replace(
        guide,
        sections=[
            guide.sections[0],
            replace(
                guide.sections[1],
                content=(
                    "법 제1조 및 「가상폐기물법 시행규칙」 제1조에 따라 운반 의무를 확인한다."
                ),
            ),
        ],
    )
    repository.ingest_many([law, rule, guide])
    seed = next(h for h in search(repository, "운반") if h.heading == "제2조 운반")
    basis = cited_basis(
        repository.database,
        [seed],
        Vector(repository.embeddings.embed_query("운반")),
        repository._results,
        include_seed_sections=True,
        prefer_statutes=True,
    )
    assert len(basis) == 2 and basis[0].title == law.title
    assert basis[0].evidence_role == "cited_basis"
    assert basis[0].reference_sources == (f"legal-chunk:{seed.chunk_id}",)
