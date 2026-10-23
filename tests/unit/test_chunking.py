import pytest

from resource_ai.rag.ingestion.chunker import CORPUS_CHUNK_POLICIES, chunk_for_corpus, chunk_text


def test_short_text_stays_in_one_chunk() -> None:
    chunks = chunk_text("제1항 내용입니다.")
    assert [chunk.content for chunk in chunks] == ["제1항 내용입니다."]


def test_long_text_is_split_with_size_limit() -> None:
    chunks = chunk_text("가" * 250, max_chars=100, overlap_chars=10)
    assert len(chunks) == 3
    assert all(len(chunk.content) <= 100 for chunk in chunks)


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_text("내용", max_chars=100, overlap_chars=100)


def test_each_corpus_has_an_explicit_chunk_policy() -> None:
    assert {item.value for item in CORPUS_CHUNK_POLICIES} == {
        "LAW",
        "PRECEDENT",
        "TECHNICAL_LITERATURE",
        "HISTORICAL_CASE",
        "COMPANY_DOCUMENT",
    }


def test_chunk_for_corpus_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported corpus type"):
        chunk_for_corpus("본문", "UNKNOWN")
