from resource_ai.rag.ingestion.chunker import chunk_text
from resource_ai.rag.ingestion.loader import parse_document


def test_document_can_be_parsed_and_chunked() -> None:
    document = parse_document(
        {
            "source_id": "law-1",
            "document_type": "LAW",
            "title": "테스트 법령",
            "source_url": "https://example.com/law-1",
            "sections": [{"heading": "제1조", "content": "목적 조문입니다."}],
        }
    )

    chunks = chunk_text(document.sections[0].content)

    assert [chunk.content for chunk in chunks] == ["목적 조문입니다."]
