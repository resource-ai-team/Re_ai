"""검수되고 해시로 고정된 공개 근거를 기술문헌 검색용 요약으로 변환한다."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from resource_ai.evidence.models import CorpusType, EvidenceDocument, EvidenceSection
from resource_ai.knowledge_graph.dataset import allowed_cards, read_bundle


def _load_scope(path: Path) -> dict[str, Any]:
    try:
        scope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid public evidence scope: {exc}") from exc

    if scope.get("document_type") != CorpusType.TECHNICAL_LITERATURE:
        raise ValueError("Public evidence summaries must use TECHNICAL_LITERATURE")
    lanes = scope.get("included_lanes")
    if not isinstance(lanes, list) or not lanes or not all(isinstance(item, str) for item in lanes):
        raise ValueError("Public evidence scope requires a non-empty included_lanes list")
    if scope.get("raw_document_indexing") is not False:
        raise ValueError("Public evidence builder only supports reviewed summaries")
    return scope


def _version(source: dict[str, Any]) -> str:
    checked_on = source.get("checked_on") or source.get("checked_date") or "undated"
    digest = source.get("sha256")
    if not digest:
        raise ValueError(f"Source {source.get('source_id')!r} has no verified SHA-256")
    return f"{checked_on}-{digest[:12]}"


def _section(card: dict[str, Any]) -> EvidenceSection:
    paragraphs = [f"검수 요약: {card['claim_ko']}"]
    if card.get("scope"):
        paragraphs.append(f"적용 범위: {card['scope']}")
    if card.get("limitations"):
        paragraphs.append("한계: " + "; ".join(card["limitations"]))
    if card.get("not_proven"):
        paragraphs.append("확인되지 않은 사항: " + "; ".join(card["not_proven"]))

    locator = card["locator"]
    return EvidenceSection(
        heading=card["title"],
        content="\n".join(paragraphs),
        citation=locator,
        metadata={
            "source_position": locator,
            "evidence_card_id": card["card_id"],
            "content_form": "reviewed-factual-summary",
            "collection_lane": card.get("collection_lane"),
            "material": card.get("material"),
            "geography": card.get("geography"),
            "use_for": card.get("use_for", []),
            "limitations": card.get("limitations", []),
            "not_proven": card.get("not_proven", []),
            "dispatch_approved": card.get("dispatch_approved", False),
        },
    )


def build_public_evidence_summaries(scope_path: Path) -> list[EvidenceDocument]:
    """해시와 검수 상태가 확인된 공개 근거만 검색 가능한 요약으로 만든다."""
    scope = _load_scope(scope_path)
    lanes = set(scope["included_lanes"])
    sources = {item["source_id"]: item for item in read_bundle("source-registry.json")}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for card in allowed_cards().values():
        if card.get("collection_lane") in lanes:
            grouped[card["source_id"]].append(card)

    documents: list[EvidenceDocument] = []
    for source_key, cards in sorted(grouped.items()):
        source = sources[source_key]
        version = _version(source)
        canonical_source_id = f"public-evidence:{source_key}"
        documents.append(
            EvidenceDocument(
                source_id=f"{canonical_source_id}:{version}",
                document_type=CorpusType.TECHNICAL_LITERATURE,
                title=source["title"],
                source_url=cards[0]["source_url"],
                source_org=source.get("publisher") or "발행기관 미상",
                version=version,
                sections=[
                    _section(card) for card in sorted(cards, key=lambda item: item["card_id"])
                ],
                metadata={
                    "canonical_source_id": canonical_source_id,
                    "dataset_version": scope["dataset_version"],
                    "content_form": scope["content_form"],
                    "source_sha256": source["sha256"],
                    "checked_on": source.get("checked_on") or source.get("checked_date"),
                    "source_class": source.get("source_class"),
                    "rights": {
                        "indexing_allowed": True,
                        "raw_indexing_allowed": False,
                        "model_transfer_allowed": False,
                        "rights_status": source.get("rights_status"),
                    },
                },
            )
        )

    if not documents:
        raise ValueError("Public evidence scope produced no documents")
    return documents
