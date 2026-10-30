"""현행 법령·행정규칙을 팀의 EvidenceDocument 계약에 맞춰 보강한다.

원본 XML은 로컬 캐시에 보관하고, 검색 결과의 시행일과 본문의 식별 정보를
함께 확인한다. API 오류에 포함될 수 있는 인증값은 진행 기록에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.ingestion.dataset import validate_documents
from resource_ai.rag.ingestion.official_parsing import _clean, _date, _text, parse_law_document


def normalized_title(value: str) -> str:
    # 법령명 표기상 공백과 가운뎃점만 허용한다. 부분·유사 제목은 선택하지 않는다.
    return re.sub(r"[\sㆍ·]", "", value)


def select_current_record(xml: str, title: str, kind: str, as_of: date) -> ET.Element:
    root = ET.fromstring(xml)
    tag, title_tag = ("law", "법령명한글") if kind == "law" else ("admrul", "행정규칙명")
    matches = []
    for node in root.findall(tag):
        if normalized_title(_text(node, title_tag)) != normalized_title(title):
            continue
        effective = _date(_text(node, "시행일자"))
        status = _text(node, "현행연혁코드" if kind == "law" else "현행연혁구분")
        if effective is not None and effective <= as_of and status == "현행":
            matches.append(node)
    if len(matches) != 1:
        raise ValueError(f"Expected one effective {kind} for {title!r}; found {len(matches)}")
    return matches[0]


def _annex_coverage(root: ET.Element) -> dict:
    units = [n for n in root.findall(".//별표단위") if _text(n, "별표구분") in {"", "별표"}]
    return {
        "annex_count": len(units),
        "annex_text_count": sum(bool(_clean(_text(n, "별표내용"))) for n in units),
        "annex_text_missing": [
            {"number": _text(n, "별표번호"), "title": _text(n, "별표제목")}
            for n in units
            if not _clean(_text(n, "별표내용"))
        ],
        "images_in_xml": "<img" in ET.tostring(root, encoding="unicode"),
    }


def parse_administrative_rule(xml: str) -> EvidenceDocument:
    """훈령·예규·고시를 LAW로 저장하되 법률과의 종류·원문 위치를 구분한다."""
    root = ET.fromstring(xml)
    info = root.find("행정규칙기본정보")
    if info is None or _text(info, "현행여부") != "Y":
        raise ValueError("Administrative rule is missing current official metadata")
    serial, rule_id = _text(info, "행정규칙일련번호"), _text(info, "행정규칙ID")
    title, issued = _text(info, "행정규칙명"), _text(info, "발령일자")
    effective = _date(_text(info, "시행일자"))
    if not serial or not rule_id or not title or effective is None:
        raise ValueError("Administrative rule identity or effective date is missing")
    # 조문형 규칙은 조문내용이 형제 노드로 반복된다. findtext는 첫 조문만 반환한다.
    body = "\n".join(_clean(n.text or "") for n in root.findall("조문내용"))
    sections: list[EvidenceSection] = []
    # 자유 형식 지침은 장·절의 줄 경계를 보존한다. 의미를 LLM으로 재작성하지 않는다.
    groups = re.split(r"(?m)(?=^(?:제\d+조(?:의\d+)?[ (]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.))", body)
    offset = 0
    for group in groups:
        if not group.strip():
            continue
        content = group.strip()
        if re.fullmatch(r"제\d+[편장절관]\s+[^\n]+", content):
            continue
        heading = content.splitlines()[0][:160]
        article = re.match(r"^(제\d+조(?:의\d+)?)", heading)
        article_title = re.match(r"제\d+조(?:의\d+)?\(([^)]+)\)", heading)
        sections.append(
            EvidenceSection(
                heading=heading,
                content=content,
                citation=f"{title} {heading}",
                metadata={
                    "level": "administrative_body",
                    "article": article[1] if article else None,
                    "article_title": article_title[1] if article_title else None,
                    "source_position": f"조문내용 문자 {offset}부터",
                    "legal_source_kind": "administrative_rule",
                    "hierarchy_path": [title, heading],
                },
            )
        )
        offset += len(group)
    for n in root.findall(".//별표단위"):
        if _text(n, "별표구분") not in {"", "별표"}:
            continue
        content = _clean(_text(n, "별표내용"))
        if not content:
            continue
        number = str(int(_text(n, "별표번호") or "0"))
        branch = int(_text(n, "별표가지번호") or "0")
        number += f"의{branch}" if branch else ""
        label = f"별표 {number}"
        sections.append(
            EvidenceSection(
                heading=f"{label} {_text(n, '별표제목')}",
                content=content,
                citation=f"{title} {label}",
                metadata={
                    "level": "annex",
                    "annex": number,
                    "legal_source_kind": "administrative_rule",
                    "hierarchy_path": [title, label],
                },
            )
        )
    if not sections:
        raise ValueError(f"No searchable administrative rule text: {title}")
    attachment_nodes = root.find("첨부파일")
    attachments, name = [], None
    for node in attachment_nodes if attachment_nodes is not None else []:
        if node.tag == "첨부파일명":
            name = (node.text or "").strip()
        elif node.tag == "첨부파일링크" and name:
            attachments.append({"name": name, "url": (node.text or "").strip()})
            name = None
    return EvidenceDocument(
        source_id=f"admrul:{rule_id}:{serial}",
        document_type="LAW",
        title=title,
        source_url=f"https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq={serial}",
        source_org=_text(info, "소관부처명"),
        document_no=_text(info, "발령번호"),
        version=issued,
        effective_from=effective,
        sections=sections,
        metadata={
            "canonical_source_id": f"admrul:{rule_id}",
            "rule_id": rule_id,
            "administrative_serial": serial,
            "authority": "국가법령정보센터",
            "legal_source_kind": "administrative_rule",
            "instrument_type": _text(info, "행정규칙종류"),
            "attachments": attachments,
            "attachment_only_body": "자세한 내용은 상단 메뉴" in body,
            "data_status": "REFERENCE",
            **_annex_coverage(root),
        },
    )


class CachedOfficialCollector:
    """실행 날짜별 XML 캐시·해시·제한된 재시도로 재수집을 이어간다."""

    def __init__(
        self, client: LawOpenApiClient, cache_dir: Path, as_of: date, *, interval: float = 0.4
    ) -> None:
        self.client, self.cache_dir, self.as_of = client, cache_dir, as_of
        self.interval = interval
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _request(self, key: str, method, *args: str) -> tuple[str, str]:
        path = self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".xml")
        if path.exists():
            # Windows의 자동 줄바꿈 변환 없이 캐시 바이트와 해시를 일치시킨다.
            xml = path.read_bytes().decode("utf-8")
        else:
            for attempt in range(3):
                time.sleep(self.interval * (attempt + 1))
                try:
                    xml = method(*args)
                    root = ET.fromstring(xml)
                    if root.tag not in {"LawSearch", "법령", "AdmRulSearch", "AdmRulService"}:
                        raise ValueError("Unexpected official XML response")
                    path.write_bytes(xml.encode("utf-8"))
                    break
                except (httpx.HTTPError, ET.ParseError):
                    if attempt == 2:
                        # HTTPError.__str__ can contain OC; never persist it.
                        raise ValueError(
                            "Official API transport/XML failed after 3 attempts"
                        ) from None
        return xml, hashlib.sha256(xml.encode()).hexdigest()

    def collect(self, title: str, kind: str, topic: str, version: str) -> EvidenceDocument:
        search = (
            self.client.search_effective_laws
            if kind == "law"
            else self.client.search_administrative_rules
        )
        xml, _ = self._request(f"{self.as_of}:search:{kind}:{title}", search, title)
        item = select_current_record(xml, title, kind, self.as_of)
        if kind == "law":
            mst, effective = _text(item, "법령일련번호"), _text(item, "시행일자")
            xml, raw_hash = self._request(
                f"eflaw:{mst}:{effective}",
                self.client.fetch_effective_law,
                mst,
                effective,
            )
            doc = parse_law_document(xml)
            if (
                doc.metadata["law_id"] != _text(item, "법령ID")
                or normalized_title(doc.title) != normalized_title(title)
                or doc.effective_from != _date(effective)
            ):
                raise ValueError("Law body does not match selected effective version")
            doc = replace(
                doc,
                source_id=f"law:{doc.metadata['law_id']}:{mst}:{effective}",
                source_url=f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={mst}&efYd={effective}",
                metadata={
                    **doc.metadata,
                    "legal_source_kind": "statute",
                    "selected_mst": mst,
                    **_annex_coverage(ET.fromstring(xml)),
                },
            )
        else:
            serial = _text(item, "행정규칙일련번호")
            xml, raw_hash = self._request(
                f"admrul:{serial}",
                self.client.fetch_administrative_rule,
                serial,
            )
            doc = parse_administrative_rule(xml)
            if (
                doc.metadata["administrative_serial"] != serial
                or normalized_title(doc.title) != normalized_title(title)
                or doc.effective_from != _date(_text(item, "시행일자"))
            ):
                raise ValueError("Administrative body does not match selected version")
        if doc.effective_from is None or doc.effective_from > self.as_of:
            raise ValueError("Future or undated legal version is not indexable")
        doc = replace(
            doc,
            metadata={
                **doc.metadata,
                "topic": topic,
                "raw_xml_sha256": raw_hash,
                "dataset_version": version,
                "as_of": self.as_of.isoformat(),
                "collected_at": datetime.now(UTC).isoformat(),
                "coverage": "body_and_available_annex_text",
                "supplementary_provisions_indexed": False,
                "model_transfer_allowed": True,
            },
        )
        errors = validate_documents([doc]).errors
        if errors:
            raise ValueError("; ".join(errors))
        return doc


def content_fingerprint(document: EvidenceDocument) -> str:
    """수집 시간 변경으로 같은 원문을 다시 임베딩하지 않도록 내용만 해싱한다."""
    payload = {
        "title": document.title,
        "version": document.version,
        "source_url": document.source_url,
        "effective_from": str(document.effective_from),
        "sections": [
            (s.heading, s.content, s.citation, s.page_no, s.metadata) for s in document.sections
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
