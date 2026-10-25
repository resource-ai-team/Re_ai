from dataclasses import replace
from datetime import date

import pytest

from resource_ai.evidence.models import (
    EvidenceDocument,
    EvidenceSection,
    LegalDocument,
    LegalSection,
    SearchGroup,
    corpus_types_for_search_group,
)
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.service import LegalRepository


class UnusedDatabase:
    def connect(self):  # pragma: no cover - empty input must fail before DB access
        raise AssertionError("database must not be accessed")


def _law() -> LegalDocument:
    return LegalDocument(
        source_id="law:001771:20260317",
        document_type="LAW",
        title="폐기물관리법",
        source_url="https://www.law.go.kr/법령/폐기물관리법",
        source_org="기후에너지환경부",
        document_no="21467",
        effective_from=date(2026, 9, 18),
        version="20260317",
        sections=[
            LegalSection(
                heading="제17조",
                content="사업장폐기물배출자의 의무",
                citation="폐기물관리법 제17조",
            )
        ],
        metadata={"law_id": "001771"},
    )


def test_prepare_document_adds_stable_canonical_source_id() -> None:
    repository = LegalRepository(UnusedDatabase(), HashEmbeddingProvider())

    document, chunks, vectors = repository._prepare_document(_law())

    assert document.metadata["canonical_source_id"] == "law:001771"
    assert len(chunks) == len(vectors) == 1


def test_explicit_canonical_source_id_is_preserved() -> None:
    repository = LegalRepository(UnusedDatabase(), HashEmbeddingProvider())
    document = replace(_law(), metadata={"canonical_source_id": "custom:law"})

    prepared, _, _ = repository._prepare_document(document)

    assert prepared.metadata["canonical_source_id"] == "custom:law"


def test_ingest_many_rejects_empty_dataset_before_database_access() -> None:
    repository = LegalRepository(UnusedDatabase(), HashEmbeddingProvider())

    with pytest.raises(ValueError, match="At least one document"):
        repository.ingest_many([])


def test_prepare_company_document_records_tenant_and_source_metadata() -> None:
    repository = LegalRepository(UnusedDatabase(), HashEmbeddingProvider())
    document = EvidenceDocument(
        source_id="report:1:v1",
        document_type="COMPANY_DOCUMENT",
        title="성적서",
        source_url="https://example.org/report/1",
        version="v1",
        tenant_id="tenant-a",
        sections=[EvidenceSection("결과", "수분 5%", page_no=2)],
        metadata={"canonical_source_id": "company_document:report:1"},
    )

    prepared, chunks, _ = repository._prepare_document(document)

    assert prepared.metadata["canonical_source_id"] == "company_document:report:1"
    assert chunks[0][4]["corpus_type"] == "COMPANY_DOCUMENT"
    assert chunks[0][4]["tenant_id"] == "tenant-a"
    assert chunks[0][4]["source_position"] == 2


def test_search_row_keeps_version_and_source_position() -> None:
    rows = [
        (
            1,
            "성적서",
            "COMPANY_DOCUMENT",
            "시험 결과",
            "수분 5%",
            None,
            "https://example.org/report/1",
            None,
            0.02,
            "v2",
            3,
            "표 1 / 수분",
        )
    ]

    result = LegalRepository._results(rows, "vector")[0]

    assert result.version == "v2"
    assert result.page_no == 3
    assert result.source_position == "표 1 / 수분"


class EmptyCursor:
    def __init__(self, calls):
        self.calls = calls

    def execute(self, query, params):
        self.calls.append((query, params))
        return self

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class SearchDatabase:
    def __init__(self):
        self.calls = []

    def connect(self):
        return EmptyCursor(self.calls)


def test_company_search_requires_tenant() -> None:
    repository = LegalRepository(SearchDatabase(), HashEmbeddingProvider())

    with pytest.raises(ValueError, match="tenant_id is required"):
        repository.search("성적서", corpus_types=["COMPANY_DOCUMENT"])


def test_search_groups_resolve_to_stored_corpus_types() -> None:
    assert corpus_types_for_search_group(SearchGroup.LEGAL) == ("LAW", "PRECEDENT")
    assert corpus_types_for_search_group(SearchGroup.TECHNICAL) == ("TECHNICAL_LITERATURE",)
    assert corpus_types_for_search_group(SearchGroup.COMPANY) == ("COMPANY_DOCUMENT",)
    assert all(
        "HISTORICAL_CASE" not in corpus_types_for_search_group(group) for group in SearchGroup
    )


def test_company_search_group_requires_tenant() -> None:
    repository = LegalRepository(SearchDatabase(), HashEmbeddingProvider())

    with pytest.raises(ValueError, match="tenant_id is required"):
        repository.search("성적서", search_group=SearchGroup.COMPANY)


def test_legal_search_group_filters_law_and_precedent() -> None:
    database = SearchDatabase()
    repository = LegalRepository(database, HashEmbeddingProvider())

    assert repository.search("책임", search_group=SearchGroup.LEGAL) == []

    assert len(database.calls) == 2
    assert all(["LAW", "PRECEDENT"] in params for _, params in database.calls)


def test_vector_search_filters_group_before_ranking_candidates() -> None:
    database = SearchDatabase()
    repository = LegalRepository(database, HashEmbeddingProvider())

    assert repository.search("재활용 공정", search_group=SearchGroup.TECHNICAL) == []

    vector_query, params = database.calls[0]
    assert "WITH filtered_chunks AS MATERIALIZED" in vector_query
    assert vector_query.index("d.document_type = ANY") < vector_query.index(
        "ORDER BY embedding <=>"
    )
    assert ["TECHNICAL_LITERATURE"] in params


def test_company_search_applies_tenant_filter_to_both_queries() -> None:
    database = SearchDatabase()
    repository = LegalRepository(database, HashEmbeddingProvider())

    assert (
        repository.search(
            "성적서",
            corpus_types=["COMPANY_DOCUMENT"],
            tenant_id="tenant-a",
        )
        == []
    )

    assert len(database.calls) == 2
    assert all("d.tenant_id = %s" in query for query, _ in database.calls)
    assert all("tenant-a" in params for _, params in database.calls)
