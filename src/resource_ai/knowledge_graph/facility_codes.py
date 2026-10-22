"""Versioned official code labels for discovery, never legal/capability decisions."""

import re

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.dataset import read_bundle


def load_codebook():
    try:
        value = read_bundle("facility-code-labels.json")
        rows = value["records"]
        if (
            value["schema_version"] != 1
            or len(rows) != 313
            or len({r["code"] for r in rows}) != len(rows)
            or not re.fullmatch(r"[a-f0-9]{64}", value["source"]["pdf_sha256"])
            or any(
                not re.fullmatch(r"\d{2}-\d{2}-\d{2}", r["code"])
                or not isinstance(r["label_excerpt"], str)
                or not r["label_excerpt"]
                or type(r["page"]) is not int
                or not 1 <= r["page"] <= 14
                or r["section"] not in {"business_non_designated", "household", "designated"}
                or not isinstance(r["query_materials"], list)
                or not set(r["query_materials"]) <= {"pe", "pp", "pet", "paper", "aluminium"}
                or r["match_kind"] not in {None, "code_category_only", "named_material_code_only"}
                or bool(r["query_materials"]) != bool(r["match_kind"])
                for r in rows
            )
        ):
            raise ValueError()
        return {
            "records": {r["code"]: r for r in rows},
            "source": value["source"],
            "sha256": graph.digest(value),
        }
    except (OSError, ValueError, TypeError, KeyError):
        raise store.GraphStoreError(
            "codebook-integrity", "폐기물 코드 분류표의 원본·버전 검사가 실패했습니다."
        ) from None


def code_interpretations(codes, material, book):
    items = []
    for code in codes:
        row = book["records"].get(code)
        items.append(
            {
                "code": code,
                "label_excerpt": row["label_excerpt"] if row else None,
                "page": row["page"] if row else None,
                "section": row["section"] if row else None,
                "query_scope": "search_hint_only"
                if row and material in row["query_materials"]
                else "no_configured_material_hint"
                if row
                else "unmapped_requires_review",
                "permit_validated": False,
                "historical_code_consistency_verified": False,
            }
        )
    return items
