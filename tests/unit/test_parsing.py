import json
from pathlib import Path

import pytest

from resource_ai.rag.ingestion.loader import load_jsonl, parse_document


def test_parse_document_requires_sections() -> None:
    with pytest.raises(ValueError, match="at least one section"):
        parse_document(
            {
                "source_id": "law-1",
                "document_type": "LAW",
                "title": "법령",
                "source_url": "https://example.com",
                "sections": [],
            }
        )


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "documents.jsonl"
    path.write_text(
        json.dumps(
            {
                "source_id": "law-1",
                "document_type": "LAW",
                "title": "법령",
                "source_url": "https://example.com",
                "sections": [{"heading": "제1조", "content": "목적"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    documents = load_jsonl(path)
    assert documents[0].sections[0].heading == "제1조"


def test_parse_company_document_keeps_tenant_id() -> None:
    document = parse_document(
        {
            "source_id": "company:1:v1",
            "document_type": "COMPANY_DOCUMENT",
            "title": "성적서",
            "source_url": "https://example.org/company/1",
            "tenant_id": "tenant-a",
            "version": "v1",
            "sections": [{"heading": "결과", "content": "수분 5%", "page_no": 1}],
        }
    )

    assert document.tenant_id == "tenant-a"
    assert document.corpus_type == "COMPANY_DOCUMENT"
