import pytest

from app.chunking import chunk_text


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

