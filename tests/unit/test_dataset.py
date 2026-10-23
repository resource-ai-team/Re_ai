import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from resource_ai.evidence.models import LegalDocument, LegalSection
from resource_ai.rag.ingestion.dataset import (
    validate_documents,
    verify_dataset_manifest,
    write_dataset_manifest,
)
from resource_ai.rag.ingestion.loader import write_jsonl


def _official_document() -> LegalDocument:
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
        metadata={
            "authority": "국가법령정보센터",
            "dataset_version": "1.0.0",
        },
    )


def test_validate_documents_accepts_official_law() -> None:
    validation = validate_documents([_official_document()])

    assert validation.passed is True
    assert validation.document_count == 1
    assert validation.section_count == 1
    assert validation.document_types == {"LAW": 1}


def test_validate_documents_rejects_non_official_source() -> None:
    invalid = replace(_official_document(), source_url="https://example.com/not-official")

    validation = validate_documents([invalid])

    assert validation.passed is False
    assert "source_url must use www.law.go.kr" in validation.errors[0]


def test_manifest_records_dataset_and_scope_hashes(tmp_path: Path) -> None:
    document = _official_document()
    dataset_path = tmp_path / "documents.jsonl"
    scope_path = tmp_path / "scope.json"
    manifest_path = tmp_path / "documents.manifest.json"
    scope_path.write_text('{"dataset_version":"1.0.0"}', encoding="utf-8")
    write_jsonl(dataset_path, [document])

    write_dataset_manifest(
        manifest_path,
        documents=[document],
        scope_path=scope_path,
        dataset_path=dataset_path,
    )

    content = manifest_path.read_text(encoding="utf-8")
    assert '"dataset_version": "1.0.0"' in content
    assert '"dataset_sha256"' in content
    assert '"scope_sha256"' in content


def test_verify_dataset_manifest_accepts_unchanged_dataset(tmp_path: Path) -> None:
    document = _official_document()
    dataset_path = tmp_path / "documents.jsonl"
    scope_path = tmp_path / "scope.json"
    manifest_path = tmp_path / "documents.manifest.json"
    scope_path.write_text('{"dataset_version":"1.0.0"}', encoding="utf-8")
    write_jsonl(dataset_path, [document])
    write_dataset_manifest(
        manifest_path,
        documents=[document],
        scope_path=scope_path,
        dataset_path=dataset_path,
    )

    manifest = verify_dataset_manifest(manifest_path, dataset_path)

    assert manifest["validation"]["document_count"] == 1


def test_verify_dataset_manifest_rejects_changed_dataset(tmp_path: Path) -> None:
    dataset_path = tmp_path / "documents.jsonl"
    manifest_path = tmp_path / "documents.manifest.json"
    dataset_path.write_text("original", encoding="utf-8")
    manifest_path.write_text(
        json.dumps({"dataset_sha256": "0" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        verify_dataset_manifest(manifest_path, dataset_path)
