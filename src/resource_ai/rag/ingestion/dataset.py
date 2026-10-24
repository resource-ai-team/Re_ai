"""RAG 문서의 공통 필드와 코퍼스별 적재 조건을 검증한다."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from resource_ai.evidence.models import CorpusType, EvidenceDocument

_CORPUS_TYPE_VALUES = frozenset(item.value for item in CorpusType)


@dataclass(frozen=True)
class DatasetValidation:
    document_count: int
    section_count: int
    document_types: dict[str, int]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _validate_common(document: EvidenceDocument, errors: list[str]) -> None:
    prefix = document.source_id or document.title
    if document.document_type not in _CORPUS_TYPE_VALUES:
        errors.append(f"{prefix}: unsupported corpus type {document.document_type!r}")
        return
    parsed_url = urlparse(document.source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        errors.append(f"{prefix}: source_url must be an absolute HTTP(S) URL")
    if not document.version:
        errors.append(f"{prefix}: source version is missing")
    if not document.sections:
        errors.append(f"{prefix}: document has no sections")
    for index, section in enumerate(document.sections):
        if not section.heading or not section.content:
            errors.append(f"{prefix}: section {index} has empty heading or content")
        if not (section.citation or section.page_no or section.metadata.get("source_position")):
            errors.append(f"{prefix}: section {index} has no source position")


def _validate_corpus_policy(document: EvidenceDocument, errors: list[str]) -> None:
    """출처·권리·기업 격리 등 문서 유형별 필수 조건을 검사한다."""
    prefix = document.source_id or document.title
    corpus_type = document.document_type
    if corpus_type in {CorpusType.LAW, CorpusType.PRECEDENT}:
        hostname = (urlparse(document.source_url).hostname or "").lower()
        if hostname != "www.law.go.kr":
            errors.append(f"{prefix}: source_url must use www.law.go.kr")
        if document.metadata.get("authority") != "국가법령정보센터":
            errors.append(f"{prefix}: official authority metadata is missing")
        if corpus_type == CorpusType.LAW and document.effective_from is None:
            errors.append(f"{prefix}: law effective date is missing")
        if corpus_type == CorpusType.PRECEDENT and not document.document_no:
            errors.append(f"{prefix}: precedent case number is missing")
        return

    if corpus_type == CorpusType.TECHNICAL_LITERATURE:
        if not document.metadata.get("canonical_source_id"):
            errors.append(f"{prefix}: canonical_source_id is required for version tracking")
        if not document.source_org:
            errors.append(f"{prefix}: technical literature source organization is missing")
        rights = document.metadata.get("rights")
        if not isinstance(rights, dict) or rights.get("indexing_allowed") is not True:
            errors.append(f"{prefix}: technical literature indexing rights are not recorded")
        return

    if corpus_type == CorpusType.HISTORICAL_CASE:
        if not document.metadata.get("canonical_source_id"):
            errors.append(f"{prefix}: canonical_source_id is required for version tracking")
        if document.metadata.get("verified") is not True:
            errors.append(f"{prefix}: historical case must be verified before indexing")
        return

    if corpus_type == CorpusType.COMPANY_DOCUMENT:
        if not document.metadata.get("canonical_source_id"):
            errors.append(f"{prefix}: canonical_source_id is required for version tracking")
        if not document.tenant_id:
            errors.append(f"{prefix}: company document tenant_id is missing")
        if document.metadata.get("review_status") not in {
            "UNREVIEWED",
            "REVIEWED",
            "MODIFIED",
        }:
            errors.append(f"{prefix}: company document review_status is invalid")
        if document.metadata.get("model_transfer_allowed") is not False:
            errors.append(f"{prefix}: company document model transfer must default to blocked")


def validate_documents(documents: list[EvidenceDocument]) -> DatasetValidation:
    """전체 데이터셋을 적재하기 전에 중복과 문서별 오류를 한 번에 수집한다."""
    errors: list[str] = []
    source_keys = [(document.tenant_id or "", document.source_id) for document in documents]
    duplicates = sorted(
        f"{tenant_id}:{source_id}" if tenant_id else source_id
        for (tenant_id, source_id), count in Counter(source_keys).items()
        if count > 1
    )
    if duplicates:
        errors.append(f"Duplicate source_id values: {', '.join(duplicates)}")

    for document in documents:
        _validate_common(document, errors)
        if document.document_type in _CORPUS_TYPE_VALUES:
            _validate_corpus_policy(document, errors)

    type_counts = dict(sorted(Counter(document.document_type for document in documents).items()))
    return DatasetValidation(
        document_count=len(documents),
        section_count=sum(len(document.sections) for document in documents),
        document_types=type_counts,
        errors=tuple(errors),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_dataset_manifest(manifest_path: Path, dataset_path: Path) -> dict:
    """생성 데이터가 기록된 매니페스트의 SHA-256과 같은지 확인한다."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid dataset manifest: {exc}") from exc

    expected_hash = manifest.get("dataset_sha256")
    if not expected_hash:
        raise ValueError("Dataset manifest is missing dataset_sha256")
    actual_hash = _sha256(dataset_path)
    if actual_hash != expected_hash:
        raise ValueError(
            "Dataset SHA-256 does not match the manifest "
            f"(expected {expected_hash}, got {actual_hash})"
        )
    return manifest


def write_dataset_manifest(
    path: Path,
    *,
    documents: list[EvidenceDocument],
    scope_path: Path,
    dataset_path: Path,
) -> None:
    validation = validate_documents(documents)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": (
            documents[0].metadata.get("dataset_version", "unversioned")
            if documents
            else "unversioned"
        ),
        "scope_file": str(scope_path),
        "scope_sha256": _sha256(scope_path),
        "dataset_file": str(dataset_path),
        "dataset_sha256": _sha256(dataset_path),
        "validation": asdict(validation),
        "sources": [
            {
                "source_id": document.source_id,
                "document_type": document.document_type,
                "title": document.title,
                "version": document.version,
                "effective_from": (
                    document.effective_from.isoformat() if document.effective_from else None
                ),
                "source_url": document.source_url,
                "tenant_id": document.tenant_id,
                "section_count": len(document.sections),
            }
            for document in documents
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
