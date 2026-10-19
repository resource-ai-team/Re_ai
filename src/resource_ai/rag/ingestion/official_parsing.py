from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import quote

from resource_ai.evidence.models import LegalDocument, LegalSection
from resource_ai.integrations.law_api import LawSearchItem, PrecedentSearchItem

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _text(element: ET.Element, name: str) -> str:
    return (element.findtext(name) or "").strip()


def _clean(value: str) -> str:
    value = _BR_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in html.unescape(value).splitlines()]
    return "\n".join(line for line in lines if line)


def _date(value: str) -> date | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        return None
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))


def parse_law_search(xml_text: str) -> list[LawSearchItem]:
    root = ET.fromstring(xml_text)
    return [
        LawSearchItem(
            law_id=_text(item, "법령ID"),
            mst=_text(item, "법령일련번호"),
            title=_text(item, "법령명한글"),
            promulgation_date=_text(item, "공포일자"),
            effective_date=_text(item, "시행일자"),
            ministry=_text(item, "소관부처명") or None,
        )
        for item in root.findall("law")
    ]


def parse_precedent_search(xml_text: str) -> list[PrecedentSearchItem]:
    root = ET.fromstring(xml_text)
    return [
        PrecedentSearchItem(
            precedent_id=_text(item, "판례일련번호"),
            case_name=_text(item, "사건명"),
            case_number=_text(item, "사건번호"),
            decision_date=_text(item, "선고일자"),
            court_name=_text(item, "법원명"),
        )
        for item in root.findall("prec")
    ]


def article_code(article_number: str) -> str:
    match = re.fullmatch(r"(\d+)(?:의(\d+))?", article_number.strip())
    if not match:
        raise ValueError(f"Unsupported article number: {article_number}")
    number, branch = match.groups()
    return f"{int(number):04d}{int(branch or 0):02d}"


def _article_label(unit: ET.Element) -> str:
    number = _text(unit, "조문번호")
    branch = _text(unit, "조문가지번호")
    return f"제{number}조" + (f"의{branch}" if branch else "")


def _descendant_text(element: ET.Element) -> list[str]:
    values: list[str] = []
    for tag in ("호내용", "목내용"):
        values.extend(_clean(node.text or "") for node in element.findall(f".//{tag}"))
    return [value for value in values if value]


def parse_law_document(
    xml_text: str,
    *,
    selected_annexes: set[str] | None = None,
    include_articles: bool = True,
) -> LegalDocument:
    root = ET.fromstring(xml_text)
    info = root.find("기본정보")
    if info is None:
        raise ValueError("Law response is missing 기본정보")

    title = _text(info, "법령명_한글")
    law_id = _text(info, "법령ID")
    promulgation_date = _text(info, "공포일자")
    effective_date = _text(info, "시행일자")
    source_url = f"https://www.law.go.kr/법령/{quote(title)}"
    sections: list[LegalSection] = []

    for unit in root.findall("./조문/조문단위") if include_articles else []:
        article = _article_label(unit)
        article_title = _text(unit, "조문제목")
        article_heading = f"{article}({article_title})" if article_title else article
        article_text = _clean(_text(unit, "조문내용"))
        paragraphs = unit.findall("항")
        if not paragraphs:
            if article_text:
                sections.append(
                    LegalSection(
                        heading=article_heading,
                        content=article_text,
                        citation=f"{title} {article}",
                        metadata={
                            "level": "article",
                            "article": article,
                            "article_title": article_title,
                            "hierarchy_path": [title, article],
                            "article_effective_date": _text(unit, "조문시행일자"),
                        },
                    )
                )
            continue

        for paragraph in paragraphs:
            paragraph_no = _text(paragraph, "항번호")
            content_parts = [article_text, _clean(_text(paragraph, "항내용"))]
            content_parts.extend(_descendant_text(paragraph))
            content = "\n".join(dict.fromkeys(part for part in content_parts if part))
            heading = f"{article_heading} {paragraph_no}항" if paragraph_no else article_heading
            citation = (
                f"{title} {article} {paragraph_no}항" if paragraph_no else f"{title} {article}"
            )
            sections.append(
                LegalSection(
                    heading=heading,
                    content=content,
                    citation=citation,
                    metadata={
                        "level": "paragraph",
                        "article": article,
                        "article_title": article_title,
                        "paragraph": paragraph_no,
                        "hierarchy_path": [title, article, paragraph_no],
                        "article_effective_date": _text(unit, "조문시행일자"),
                    },
                )
            )

    for annex in root.findall(".//별표단위"):
        if _text(annex, "별표구분") not in {"", "별표"}:
            continue
        number = str(int(_text(annex, "별표번호")))
        branch_value = int(_text(annex, "별표가지번호") or 0)
        normalized = number + (f"의{branch_value}" if branch_value else "")
        if selected_annexes is not None and normalized not in selected_annexes:
            continue
        annex_title = _text(annex, "별표제목")
        content = _clean(_text(annex, "별표내용"))
        if not content:
            continue
        label = f"별표 {normalized}"
        sections.append(
            LegalSection(
                heading=f"{label} {annex_title}".strip(),
                content=content,
                citation=f"{title} {label}",
                metadata={
                    "level": "annex",
                    "annex": normalized,
                    "hierarchy_path": [title, label],
                    "pdf_path": _text(annex, "별표서식PDF파일링크") or None,
                },
            )
        )

    if not sections:
        raise ValueError(f"No searchable sections found for {title}")
    return LegalDocument(
        source_id=f"law:{law_id}:{promulgation_date}",
        document_type="LAW",
        title=title,
        source_url=source_url,
        source_org=_text(info, "소관부처") or None,
        document_no=_text(info, "공포번호") or None,
        effective_from=_date(effective_date),
        version=promulgation_date,
        sections=sections,
        metadata={
            "law_id": law_id,
            "promulgation_date": promulgation_date,
            "effective_date": effective_date,
            "revision_type": _text(info, "제개정구분"),
            "authority": "국가법령정보센터",
            "data_status": "REFERENCE",
        },
    )


def merge_law_documents(documents: list[LegalDocument]) -> LegalDocument:
    if not documents:
        raise ValueError("At least one law document is required")
    first = documents[0]
    if any(document.source_id != first.source_id for document in documents):
        raise ValueError("Only identical law versions can be merged")
    sections = [section for document in documents for section in document.sections]
    return LegalDocument(
        source_id=first.source_id,
        document_type=first.document_type,
        title=first.title,
        source_url=first.source_url,
        sections=sections,
        source_org=first.source_org,
        document_no=first.document_no,
        effective_from=first.effective_from,
        version=first.version,
        metadata=first.metadata,
    )


def parse_precedent_document(xml_text: str) -> LegalDocument:
    root = ET.fromstring(xml_text)
    precedent_id = _text(root, "판례정보일련번호")
    case_name = _text(root, "사건명")
    case_number = _text(root, "사건번호")
    decision_date = _text(root, "선고일자")
    court = _text(root, "법원명")
    sections: list[LegalSection] = []
    for tag, heading in (
        ("판시사항", "판시사항"),
        ("판결요지", "판결요지"),
        ("참조조문", "참조조문"),
        ("참조판례", "참조판례"),
        ("판례내용", "판례 전문"),
    ):
        content = _clean(_text(root, tag))
        if content:
            sections.append(
                LegalSection(
                    heading=heading,
                    content=content,
                    citation=f"{court} {case_number} {heading}",
                    metadata={
                        "level": "case_section",
                        "case_number": case_number,
                        "section_type": tag,
                        "hierarchy_path": [case_number, heading],
                    },
                )
            )
    if not sections:
        raise ValueError(f"No searchable sections found for precedent {case_number}")
    return LegalDocument(
        source_id=f"precedent:{precedent_id}",
        document_type="PRECEDENT",
        title=f"{court} {case_number} {case_name}",
        source_url=f"https://www.law.go.kr/precInfoP.do?precSeq={precedent_id}",
        source_org=court,
        document_no=case_number,
        version=decision_date,
        sections=sections,
        metadata={
            "precedent_id": precedent_id,
            "case_name": case_name,
            "case_number": case_number,
            "decision_date": decision_date,
            "judgment_type": _text(root, "판결유형"),
            "authority": "국가법령정보센터",
            "data_status": "REFERENCE",
        },
    )
