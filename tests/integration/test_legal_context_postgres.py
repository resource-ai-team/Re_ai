import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.rag.legal_context import expand_context
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


def test_actual_parent_exception_explicit_links_versions_and_budgets():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    url = get_settings().database_url
    schema = "re_ai_context_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = EvidenceRepository(
            Database(make_conninfo(url, options=f"-c search_path={schema},public")),
            HashEmbeddingProvider(),
        )
        repo.initialize(Path("migrations"))

        def doc(title, source, sections, tenant=None):
            return EvidenceDocument(
                source,
                "LAW",
                title,
                "https://www.law.go.kr/법령/가상",
                sections,
                effective_from=date(2020, 1, 1),
                version="v1",
                tenant_id=tenant,
            )

        repo.ingest(
            doc(
                "폐기물관리법",
                "synthetic-law",
                [
                    EvidenceSection(
                        "제25조 ①항",
                        "① 「폐기물관리법 시행규칙」 제28조에 따른 가상 허가",
                        "폐기물관리법 제25조 ①항",
                        metadata={"article": "제25조", "article_title": "가상허가"},
                    ),
                    EvidenceSection(
                        "제25조 ②항",
                        "② 다만, 연구용 예외는 별도 확인한다.",
                        "폐기물관리법 제25조 ②항",
                        metadata={"article": "제25조", "article_title": "가상허가"},
                    ),
                    EvidenceSection(
                        "제25조의3",
                        "다른 조문",
                        "폐기물관리법 제25조의3",
                        metadata={"article": "제25조의3", "article_title": "가상다른조문"},
                    ),
                ],
            )
        )
        repo.ingest(
            doc(
                "폐기물관리법 시행규칙",
                "synthetic-rule",
                [
                    EvidenceSection(
                        "제28조",
                        "가상 기준은 별표 5의3에 따른다.",
                        "폐기물관리법 시행규칙 제28조",
                        metadata={"article": "제28조", "article_title": "가상기준"},
                    ),
                    EvidenceSection(
                        "별표 5의3",
                        "가상 부속서 본문",
                        "폐기물관리법 시행규칙 별표 5의3",
                        metadata={"annex": "5의3"},
                    ),
                ],
            )
        )
        hits = repo.search("가상 허가", 20, search_group="LEGAL")
        seed = next(h for h in hits if h.heading == "제25조 ①항")
        context = expand_context(repo.database, [seed])[seed.chunk_id]
        assert context["status"] == "retrieved"
        assert len(context["primary"]) == 2
        assert "다만" in context["primary"][1]["content"]
        assert [r["locator"] for r in context["linked"]] == ["제28조"]
        assert context["applicability"] == "not_audited"
        rule = next(h for h in hits if h.heading == "제28조")
        assert (
            expand_context(repo.database, [rule])[rule.chunk_id]["linked"][0]["locator"]
            == "별표5의3"
        )
        annex = next(h for h in hits if h.heading == "별표 5의3")
        assert expand_context(repo.database, [annex])[annex.chunk_id]["status"] == "retrieved"
        assert expand_context(repo.database, [seed], max_chunks=1)[seed.chunk_id][
            "source_checks"
        ] == ["partial_context"]
        assert expand_context(repo.database, [seed], max_links=0)[seed.chunk_id]["unresolved"]
        changed = replace(seed, version="v0")
        assert expand_context(repo.database, [changed])[seed.chunk_id]["source_checks"] == [
            "stale_or_unresolvable_seed"
        ]
        repo.ingest(
            doc(
                "폐기물관리법",
                "synthetic-private",
                [
                    EvidenceSection(
                        "제25조",
                        "private",
                        "비공개",
                        metadata={"article": "제25조", "article_title": "비공개"},
                    )
                ],
                tenant="synthetic-tenant",
            )
        )
        assert len(expand_context(repo.database, [seed])[seed.chunk_id]["primary"]) == 2
        assert all(h.content != "private" for h in repo.search("private", 20, search_group="LEGAL"))
        repo.ingest(
            doc(
                "폐기물관리법",
                "synthetic-duplicate",
                [
                    EvidenceSection(
                        "제25조",
                        "duplicate",
                        "가상",
                        metadata={"article": "제25조", "article_title": "중복"},
                    )
                ],
            )
        )
        packet = expand_context(repo.database, [seed])[seed.chunk_id]
        assert packet["status"] == "review_required" and not packet["primary"]
    finally:
        assert schema.startswith("re_ai_context_test_") and len(schema) == 51
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
