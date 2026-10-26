import io
import sys
import zipfile
from types import SimpleNamespace

import pytest

from resource_ai.rag.ingestion.legal_attachments import (
    attach_test_standard,
    official_attachment_url,
)
from resource_ai.rag.ingestion.official_parsing import parse_law_document
from tests.unit.test_legal_expansion import BODY


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/flDownload.do?flSeq=123",
        "https://www.law.go.kr/flDownload.do?flSeq=123&OC=secret",
        "https://www.law.go.kr/other?flSeq=123",
        "https://www.law.go.kr/flDownload.do?flSeq=abc",
        "https://www.law.go.kr/flDownload.do?flSeq=123&flSeq=456",
    ],
)
def test_rejects_untrusted_or_credentialed_attachment_urls(url):
    with pytest.raises(ValueError):
        official_attachment_url(url)


def test_official_download_forces_https_and_no_original_path_extraction(tmp_path, monkeypatch):
    from dataclasses import replace

    assert official_attachment_url("http://law.go.kr/flDownload.do?flSeq=123") == (
        "https://www.law.go.kr/flDownload.do?flSeq=123"
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("../../공정기준.pdf", b"fake-pdf-for-contract-test")
        archive.writestr("ignored.xlsx", b"not-indexed")
    (tmp_path / "123.zip").write_bytes(raw.getvalue())
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(
            __version__="test",
            PdfReader=lambda _: SimpleNamespace(
                pages=[
                    SimpleNamespace(
                        extract_text=lambda: (
                            "시료채취 및 전처리 시험 방법을 원문에서 확인하는 테스트 문장"
                        )
                    ),
                    SimpleNamespace(extract_text=lambda: ""),
                ]
            ),
        ),
    )
    doc = replace(
        parse_law_document(BODY),
        title="폐기물공정시험기준",
        metadata={
            "attachment_only_body": True,
            "attachments": [
                {"name": "시험기준 전문.zip", "url": "http://law.go.kr/flDownload.do?flSeq=123"}
            ],
        },
    )
    result = attach_test_standard(doc, tmp_path)
    assert len(result.sections) == 1
    assert result.sections[0].page_no == 1
    assert result.metadata["table_visual_verification"] is False
    assert result.metadata["attachment_empty_pages"][0]["page"] == 2
    assert [p.name for p in tmp_path.iterdir()] == ["123.zip"]
