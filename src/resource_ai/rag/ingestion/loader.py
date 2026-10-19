import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from resource_ai.evidence.models import LegalDocument, LegalSection


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_document(payload: dict[str, Any]) -> LegalDocument:
    required = ("source_id", "document_type", "title", "source_url", "sections")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")
    sections = [
        LegalSection(
            heading=section["heading"],
            content=section["content"],
            citation=section.get("citation"),
            page_no=section.get("page_no"),
            metadata=section.get("metadata", {}),
        )
        for section in payload["sections"]
    ]
    if not sections:
        raise ValueError("A document must contain at least one section")
    return LegalDocument(
        source_id=payload["source_id"],
        document_type=payload["document_type"],
        title=payload["title"],
        source_url=payload["source_url"],
        sections=sections,
        source_org=payload.get("source_org"),
        document_no=payload.get("document_no"),
        effective_from=_parse_date(payload.get("effective_from")),
        version=payload.get("version"),
        metadata=payload.get("metadata", {}),
    )


def load_jsonl(path: Path) -> list[LegalDocument]:
    documents: list[LegalDocument] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                documents.append(parse_document(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
    return documents


def write_jsonl(path: Path, documents: list[LegalDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for document in documents:
            payload = asdict(document)
            if document.effective_from:
                payload["effective_from"] = document.effective_from.isoformat()
            output.write(json.dumps(payload, ensure_ascii=False) + "\n")
