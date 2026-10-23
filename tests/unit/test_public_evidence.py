import json
from pathlib import Path

import pytest

from resource_ai.evidence.models import CorpusType
from resource_ai.rag.ingestion.dataset import validate_documents
from resource_ai.rag.ingestion.public_evidence import build_public_evidence_summaries


def test_build_public_evidence_summaries_uses_only_reviewed_summary_content() -> None:
    documents = build_public_evidence_summaries(Path("data/config/public_evidence_scope.json"))

    validation = validate_documents(documents)
    assert validation.passed is True
    assert len(documents) == 33
    assert sum(len(document.sections) for document in documents) == 54
    assert {document.document_type for document in documents} == {CorpusType.TECHNICAL_LITERATURE}
    assert all(
        document.metadata["rights"]["raw_indexing_allowed"] is False for document in documents
    )
    assert all(
        section.content.startswith("검수 요약:")
        for document in documents
        for section in document.sections
    )


def test_build_public_evidence_rejects_raw_indexing_scope(tmp_path: Path) -> None:
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(
            {
                "dataset_version": "test",
                "document_type": "TECHNICAL_LITERATURE",
                "included_lanes": ["technical"],
                "content_form": "raw",
                "raw_document_indexing": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only supports reviewed summaries"):
        build_public_evidence_summaries(scope)
