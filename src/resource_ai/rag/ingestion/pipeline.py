"""고정된 수집 범위에 따라 공식 법령과 판례 원문을 재현 가능하게 수집한다."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from resource_ai.evidence.models import LegalDocument
from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.ingestion.official_parsing import (
    article_code,
    merge_law_documents,
    parse_law_document,
    parse_law_search,
    parse_precedent_document,
    parse_precedent_search,
)


def _exact_law(client: LawOpenApiClient, title: str):
    matches = [item for item in parse_law_search(client.search_laws(title)) if item.title == title]
    if len(matches) != 1:
        raise ValueError(f"Expected one current law named {title!r}, found {len(matches)}")
    return matches[0]


def collect_law(client: LawOpenApiClient, spec: dict[str, Any]) -> LegalDocument:
    """법령명과 선택 조문·별표 또는 전체 원문을 수집한다."""
    item = _exact_law(client, spec["title"])
    documents: list[LegalDocument] = []
    for article in spec.get("articles", []):
        documents.append(parse_law_document(client.fetch_law(item.law_id, article_code(article))))
    annexes = set(spec.get("annexes", []))
    if annexes:
        annex_document = parse_law_document(
            client.fetch_law(item.law_id),
            selected_annexes=annexes,
            include_articles=False,
        )
        found_annexes = {section.metadata["annex"] for section in annex_document.sections}
        if found_annexes != annexes:
            missing = ", ".join(sorted(annexes - found_annexes))
            raise ValueError(f"Missing annexes for {item.title}: {missing}")
        documents.append(annex_document)
    if not documents:
        documents.append(parse_law_document(client.fetch_law(item.law_id)))
    return merge_law_documents(documents)


def collect_precedent(client: LawOpenApiClient, case_number: str) -> LegalDocument:
    """사건번호가 정확히 일치하는 판례 한 건을 수집한다."""
    matches = [
        item
        for item in parse_precedent_search(client.search_precedents(case_number))
        if item.case_number == case_number
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one precedent numbered {case_number!r}, found {len(matches)}")
    return parse_precedent_document(client.fetch_precedent(matches[0].precedent_id))


def collect_scope(client: LawOpenApiClient, scope_path: Path) -> list[LegalDocument]:
    """범위 설정의 법령·판례를 수집하고 데이터셋 버전과 수집 시각을 기록한다."""
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    documents = [collect_law(client, spec) for spec in scope.get("laws", [])]
    documents.extend(
        collect_precedent(client, spec["case_number"]) for spec in scope.get("precedents", [])
    )
    collected_at = datetime.now(UTC).isoformat()
    return [
        replace(
            document,
            metadata={
                **document.metadata,
                "dataset_version": scope.get("dataset_version", "unversioned"),
                "scope_source": scope.get("source"),
                "collected_at": collected_at,
            },
        )
        for document in documents
    ]
