"""Pinned public-reference inputs, independent of the prototype application."""

import json
from hashlib import sha256
from pathlib import Path

RESOURCES = Path(__file__).with_name("resources")
SOURCES = RESOURCES / "source-registry.json"
DEFAULT_DATASET = Path("data/knowledge_graph/w2rkg_candidate_index.jsonl")
DATASET_SHA256 = "6c3c7ba5d04e0ea174c56a66ea99d0989082c38c4a15e8419da40f54678be54f"


def read_bundle(name: str):
    manifest = json.loads((RESOURCES / "bundle-manifest.json").read_text(encoding="utf8"))
    content = (RESOURCES / name).read_bytes()
    if sha256(content).hexdigest() != manifest["files"][name]:
        raise ValueError("Reference bundle changed: regenerate and review its manifest")
    return json.loads(content.decode("utf-8-sig"))


def allowed_cards() -> dict:
    sources = read_bundle("source-registry.json")
    cards = read_bundle("evidence-cards.json")
    registry = {s["source_id"]: s for s in sources}
    if len(registry) != len(sources) or len({c["card_id"] for c in cards}) != len(cards):
        raise ValueError("Duplicate source/card identity")
    result = {}
    for card in cards:
        source = registry.get(card.get("source_id"), {})
        if (
            not source
            or not card.get("original_verified_locally")
            or not source.get("local_original_verified")
        ):
            continue
        if any(
            value is False
            for value in [
                card.get("indexing_allowed"),
                card.get("local_research_search_eligible"),
                source.get("indexing_allowed"),
                source.get("summary_indexing_allowed"),
            ]
        ):
            continue
        if source.get("acquisition_category") in {
            "access_failure",
            "availability_page_only_full_reports_not_acquired",
            "paid_catalogue_page_only_full_guidelines_not_acquired",
        }:
            continue
        if (
            not card.get("sha256")
            or card["sha256"] != source.get("sha256")
            or card.get("raw_path") != source.get("raw_path")
        ):
            continue
        urls = {v for k, v in source.items() if "url" in k and isinstance(v, str)}
        if (
            card.get("source_url") not in urls
            or not card.get("locator")
            or not card.get("claim_ko")
        ):
            continue
        result[card["card_id"]] = card
    return result


def authored_reports() -> list[dict]:
    """Frozen research templates; never customer uploads or validated plant flows."""
    return read_bundle("authored-reports.json")


def read_candidates(path: Path = DEFAULT_DATASET) -> list[dict]:
    content = path.read_bytes()
    if sha256(content).hexdigest() != DATASET_SHA256:
        raise ValueError("W2RKG checksum mismatch: use the attributed pinned dataset")
    rows = [json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    if len(rows) != 33679:
        raise ValueError("Incomplete W2RKG dataset")
    return rows
