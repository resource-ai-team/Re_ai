from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from resource_ai.evidence.models import LegalDocument


@dataclass(frozen=True)
class DatasetValidation:
    document_count: int
    section_count: int
    document_types: dict[str, int]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def validate_documents(documents: list[LegalDocument]) -> DatasetValidation:
    errors: list[str] = []
    source_ids = [document.source_id for document in documents]
    duplicates = sorted(source_id for source_id, count in Counter(source_ids).items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate source_id values: {', '.join(duplicates)}")

    for document in documents:
        prefix = document.source_id or document.title
        hostname = (urlparse(document.source_url).hostname or "").lower()
        if hostname != "www.law.go.kr":
            errors.append(f"{prefix}: source_url must use www.law.go.kr")
        if document.metadata.get("authority") != "국가법령정보센터":
            errors.append(f"{prefix}: official authority metadata is missing")
        if not document.version:
            errors.append(f"{prefix}: source version is missing")
        if document.document_type == "LAW" and document.effective_from is None:
            errors.append(f"{prefix}: law effective date is missing")
        if document.document_type == "PRECEDENT" and not document.document_no:
            errors.append(f"{prefix}: precedent case number is missing")
        if not document.sections:
            errors.append(f"{prefix}: document has no sections")
        for index, section in enumerate(document.sections):
            if not section.heading or not section.content:
                errors.append(f"{prefix}: section {index} has empty heading or content")
            if not section.citation:
                errors.append(f"{prefix}: section {index} has no citation")

    type_counts = dict(sorted(Counter(document.document_type for document in documents).items()))
    return DatasetValidation(
        document_count=len(documents),
        section_count=sum(len(document.sections) for document in documents),
        document_types=type_counts,
        errors=tuple(errors),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_dataset_manifest(
    path: Path,
    *,
    documents: list[LegalDocument],
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
                "section_count": len(document.sections),
            }
            for document in documents
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
