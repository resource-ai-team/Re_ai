"""Conservative test-report interpretation retaining original cells and positions."""

import re
from datetime import date
from decimal import Decimal

from resource_ai.knowledge_graph.ontology import digest
from resource_ai.material_quality.models import QualityClaim


def classify(text):
    if re.search(r"용출|식품용|식품.*용기|총용출량", text):
        return "food_contact_migration"
    if re.search(r"인장|신장률|인열|굴곡|충격|두께", text):
        return "mechanical_material_test"
    if re.search(r"오염률|비PP|수분|함수율|폐기물|혼입률", text):
        return "waste_quality_candidate"
    return "uncertain_document"


def _first(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    return match[1].strip()[:180] if match else None


def _value(raw):
    raw = raw.strip()
    if re.search(r"불검출|미검출|\bND\b|not detected", raw, re.IGNORECASE):
        return None, "not_detected"
    match = re.fullmatch(r"\s*([<>≤≥]?)\s*(\d+(?:\.\d+)?)\s*", raw)
    if not match:
        return None, "unknown"
    number = Decimal(match[2])
    if not number.is_finite() or number > Decimal("1e15"):
        return None, "unknown"
    return number, {"<": "lt", "≤": "lt", ">": "gt", "≥": "gt"}.get(match[1], "exact")


def extract_report(document_id, extraction):
    pages = extraction.get("pages", [])
    text = "\n".join(p.get("text", "") for p in pages)
    category = classify(text)
    sample = _first(r"(?:시료\s*ID|시료번호|sample\s*id)\s*[:：]\s*([A-Za-z0-9_.:-]+)", text)
    sample_ids = sorted(
        set(
            re.findall(
                r"(?:시료\s*ID|시료번호|sample\s*id)\s*[:：]\s*([A-Za-z0-9_.:-]+)",
                text,
                re.IGNORECASE,
            )
        )
    )
    if len(sample_ids) > 1:
        sample = None
    metadata = {
        "sample_id": sample,
        "sample_ids": sample_ids,
        "sample_name": _first(r"시\s*료\s*명\s*[:：]\s*([^\n]+)", text),
        "report_number": _first(r"성\s*적\s*서\s*번\s*호\s*[:：]\s*([A-Za-z0-9-]+)", text),
        "issuer": next(
            (
                org
                for org in (
                    "한국건설생활환경시험연구원",
                    "한국플라스틱시험원",
                    "한국건설자재시험연구원",
                )
                if org in text.replace(" ", "")
            ),
            None,
        ),
        "measurement_date": _first(r"(?:측정일|시험일)\s*[:：]\s*(\d{4}-\d{2}-\d{2})", text),
        "metadata_status": "extracted_unconfirmed",
    }
    metadata["measurement_date_raw"] = metadata["measurement_date"]
    if metadata["measurement_date"]:
        try:
            date.fromisoformat(metadata["measurement_date"])
        except ValueError:
            metadata["measurement_date"] = None
    rows, claims = [], []
    for ti, table in enumerate(extraction.get("tables", [])[:100]):
        groups = {}
        header = {}
        for cell in table.get("cells", [])[:3000]:
            groups.setdefault(cell.get("rowIndex", 0), []).append(cell)
        for ri, cells in sorted(groups.items()):
            cells = sorted(cells, key=lambda c: c.get("columnIndex", 0))
            # Header-index mapping avoids treating a method number as the measured result.
            if ri == 0:
                header = {
                    c.get("columnIndex", i): c.get("content", "").replace(" ", "")
                    for i, c in enumerate(cells)
                }
                continue
            by_col = {c.get("columnIndex", i): c for i, c in enumerate(cells)}

            def column(labels, header=header, by_col=by_col):
                key = next(
                    (k for k, h in header.items() if any(label in h for label in labels)), None
                )
                return by_col.get(key, {})

            name = column(("시험항목", "측정항목", "항목")).get("content")
            result_cell = column(("시험결과", "측정결과", "결과값"))
            if not name or not result_cell:
                continue
            rows.append(
                {
                    "name": name,
                    "raw_result": result_cell.get("content", ""),
                    "unit": column(("단위",)).get("content", ""),
                    "test_method": column(("시험방법", "시험규격", "시험항목규격")).get("content"),
                    "bounding_regions": result_cell.get(
                        "boundingRegions", table.get("boundingRegions", [])
                    ),
                    "extraction_confidence": result_cell.get("confidence"),
                    "table_index": ti,
                    "row_index": ri,
                    "cells": cells,
                }
            )
    # Text-only fallback accepts an entire literal row, never numbers from nearby prose.
    if not rows:
        pattern = re.compile(
            r"^(오염률|오염함량|비PP 함량|수분|함수율|물량|중량)\s*[:：]?\s*(불검출|ND|[<>]?\d+(?:\.\d+)?)\s*(wt%|mg/L|kg|t|%)\s*$",
            re.IGNORECASE,
        )
        for page in pages:
            for line in page.get("text", "").splitlines():
                match = pattern.fullmatch(line.strip())
                if match:
                    rows.append(
                        {
                            "name": match[1],
                            "raw_result": match[2],
                            "unit": match[3],
                            "test_method": None,
                            "bounding_regions": [{"pageNumber": page.get("page", 1)}],
                            "extraction_confidence": None,
                        }
                    )
    for index, row in enumerate(rows):
        value, qualifier = _value(row["raw_result"])
        unit = row["unit"].strip()
        migration = unit.lower() == "mg/l" or "용출" in row["name"]
        field = "other_measurement"
        if not migration and category not in {"mechanical_material_test", "food_contact_migration"}:
            if re.search(r"오염|비PP|혼입", row["name"]):
                field = "contamination"
            elif row["name"].strip() in {"수분", "함수율"}:
                field = "moisture"
            elif row["name"].strip() in {"물량", "중량"} and unit in {"kg", "t"}:
                field = "quantity_kg"
        if field == "quantity_kg" and value is not None and unit == "t":
            value, unit = value * 1000, "kg"
            if value > Decimal("1e15"):
                value, qualifier = None, "unknown"
        # A percent with no mass denominator stays ambiguous.
        basis = (
            "wet_mass"
            if unit == "wt%" and ("습윤" in text or "wet mass" in text.lower())
            else "unknown"
        )
        regions = row.get("bounding_regions", [])
        region = regions[0] if regions else {}
        claim = QualityClaim(
            evidence_id="ocr:" + digest([document_id, index, row])[:32],
            field=field,
            source_type="document_extracted_unconfirmed",
            raw_value=row["raw_result"],
            normalized_value=value,
            unit=unit,
            meaning="migration"
            if migration
            else "mechanical"
            if category == "mechanical_material_test"
            else "lot_property"
            if field != "other_measurement"
            else "unknown",
            basis=basis,
            qualifier=qualifier,
            test_method=(row.get("test_method") or "")[:200] or None,
            sample_id=sample,
            measurement_date=metadata["measurement_date"],
            document_id=document_id,
            page=region.get("pageNumber", 1),
            bounding_region=region.get("polygon", []),
            extraction_confidence=row.get("extraction_confidence"),
            synthetic=extraction.get("synthetic", False),
        )
        row.update(
            evidence_id=claim.evidence_id,
            semantic_type=claim.meaning,
            qualifier=qualifier,
            normalized_value=str(value) if value is not None else None,
            requires_user_confirmation=True,
        )
        claims.append(claim.model_dump(mode="json"))
    if re.search(r"\bPP\b|폴리프로필렌|polypropylene", text, re.IGNORECASE):
        claims.append(
            QualityClaim(
                evidence_id="ocr:" + digest([document_id, "material"])[:32],
                field="material",
                source_type="document_extracted_unconfirmed",
                raw_value="PP",
                text_value="PP",
                document_id=document_id,
                sample_id=sample,
                page=1,
                synthetic=extraction.get("synthetic", False),
            ).model_dump(mode="json")
        )
    return {
        "document_type": category,
        "metadata": metadata,
        "rows": rows,
        "claims": claims,
        "warnings": [
            "OCR transcription requires user confirmation; test applicability and sample binding remain separate checks."
        ],
    }
