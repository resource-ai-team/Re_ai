"""공식 폐기물공정시험기준 ZIP의 PDF를 파일·페이지 위치와 함께 읽는다."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from resource_ai.evidence.models import EvidenceDocument, EvidenceSection


def official_attachment_url(url: str) -> str:
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    if (
        parsed.hostname not in {"law.go.kr", "www.law.go.kr"}
        or parsed.path != "/flDownload.do"
        or set(params) != {"flSeq"}
        or len(params["flSeq"]) != 1
        or not params["flSeq"][0].isdigit()
    ):
        raise ValueError("Attachment must be an official fixed file ID, without credentials")
    return f"https://www.law.go.kr/flDownload.do?flSeq={params['flSeq'][0]}"


def attach_test_standard(doc: EvidenceDocument, cache_dir: Path) -> EvidenceDocument:
    """본문이 안내문뿐인 시험기준은 확인한 공식 전문 ZIP으로 대체한다."""
    if not doc.metadata.get("attachment_only_body"):
        return doc
    if doc.title != "폐기물공정시험기준":
        raise ValueError("Attachment-only rule requires an explicitly reviewed attachment adapter")
    choices = [
        a
        for a in doc.metadata.get("attachments", [])
        if "전문" in a["name"] and a["name"].lower().endswith(".zip")
    ]
    if len(choices) != 1:
        raise ValueError("Expected one official full-text test standard ZIP")
    url = official_attachment_url(choices[0]["url"])
    file_id = parse_qs(urlsplit(url).query)["flSeq"][0]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{file_id}.zip"
    if not path.exists():
        # Stream to memory until validated; failed/oversize responses never become cached files.
        data = bytearray()
        with httpx.stream("GET", url, timeout=60, follow_redirects=False) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 50_000_000:
                    raise ValueError("Attachment exceeds 50 MB download budget")
        with zipfile.ZipFile(io.BytesIO(data)):
            pass
        path.write_bytes(data)
    try:
        import pypdf
    except ImportError as exc:
        raise ValueError("Install the legal-attachments optional dependency to read PDFs") from exc
    sections, files, empty_pages = [], [], []
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(n.file_size for n in members) > 150_000_000:
            raise ValueError("Attachment exceeds expanded ZIP budget")
        for member in members:
            if not member.filename.lower().endswith(".pdf"):
                continue
            if member.file_size > 25_000_000:
                raise ValueError("PDF exceeds per-member budget")
            # ZIP paths are source locators only. Never write a member path to disk.
            data = archive.read(member)
            reader = pypdf.PdfReader(io.BytesIO(data))
            if len(reader.pages) > 2000:
                raise ValueError("PDF exceeds page budget")
            files.append(
                {
                    "name": member.filename,
                    "pages": len(reader.pages),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            for page_no, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or "").strip()
                if len(text) < 20:
                    empty_pages.append({"file": member.filename, "page": page_no})
                    continue
                sections.append(
                    EvidenceSection(
                        heading=f"{Path(member.filename).stem} · PDF {page_no}쪽",
                        content=text,
                        page_no=page_no,
                        citation=f"{doc.title} / {member.filename} / PDF {page_no}쪽",
                        metadata={
                            "level": "official_attachment_page",
                            "legal_source_kind": "administrative_rule",
                            "attachment_file": member.filename,
                            "attachment_url": url,
                            "attachment_page": page_no,
                            "table_visual_verification": False,
                        },
                    )
                )
    if not sections:
        raise ValueError("Official attachment produced no usable text")
    return replace(
        doc,
        sections=sections,
        metadata={
            **doc.metadata,
            "attachment_only_body": False,
            "coverage": "official_zip_pdf_text",
            "attachment_source_url": url,
            "attachment_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "attachment_files": files,
            "attachment_empty_pages": empty_pages,
            "pdf_extractor": f"pypdf-{pypdf.__version__}",
            "table_visual_verification": False,
        },
    )
