from dataclasses import replace
from datetime import date

import pytest

from resource_ai.evidence.models import LegalDocument, LegalSection
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
