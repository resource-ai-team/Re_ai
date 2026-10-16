import json
from pathlib import Path

import pytest

from app.parsing import load_jsonl, parse_document


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

