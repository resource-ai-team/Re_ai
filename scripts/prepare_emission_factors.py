"""Read verified public factor snapshots offline; never approve a route or write to DB.

Requires openpyxl in the document runtime. Missing values remain missing.
The public source catalogue carries boundaries; numeric availability is not fitness.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from io import BytesIO
from pathlib import Path

DEFRA_HEADERS = (
    "ID",
    "Scope",
    "Level 1",
    "Level 2",
    "Level 3",
    "Level 4",
    "Column Text",
    "UOM",
    "GHG/Unit",
    "GHG Conversion Factor 2026",
)
CATEGORIES = {
    "Waste disposal": "scope3_waste_reporting_only_not_route_lca",
    "Freighting goods": "foreign_transport_component_requires_vehicle_and_boundary_review",
    "WTT- delivery vehs & freight": "foreign_upstream_fuel_component_not_tailpipe",
    "Material use": "purchased_material_production_not_waste_treatment",
}


def numeric(value):
    """Retain published zero; never turn blank/NA into zero."""
    if value is None or value in ("", "NA", "N/A"):
        return None
    if isinstance(value, bool):
        raise TypeError("Boolean is not an emission factor")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite emission factor")
    return str(result)


def verified_content(root, source):
    content = (root / source["file"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != source["sha256"]:
        raise ValueError(f"Snapshot hash mismatch: {source['id']}")
    if len(content) != source["bytes"]:
        raise ValueError(f"Snapshot length mismatch: {source['id']}")
    return content


def record(source_id, locator, value, **fields):
    return {
        "source_id": source_id,
        "locator": locator,
        "factor_value": numeric(value),
        "value_status": "not_available" if numeric(value) is None else "published",
        "raw_value": value,
        "automatic_kr_route_ranking_admission": False,
        "facility_measurement": False,
        "model_transfer_enabled": False,
        **fields,
    }


def extract_defra(rows, source_id):
    """Read July-revised 2026 flat format with exact header/identity checks."""
    if len(rows) < 7 or tuple(rows[5][:10]) != DEFRA_HEADERS:
        raise ValueError("DEFRA columns changed; review the workbook")
    records, seen = [], set()
    for row_no, row in enumerate(rows[6:], 7):
        if not isinstance(row[0], str) or not re.fullmatch(r"\d+(?:_\d+)+", row[0]):
            continue
        if row[0] in seen:
            raise ValueError("Duplicate DEFRA ID")
        seen.add(row[0])
        if row[2] not in CATEGORIES:
            continue
        records.append(
            record(
                source_id,
                f"Factors by Category!A{row_no}:J{row_no}",
                row[9],
                factor_id=row[0],
                scope=row[1],
                category=row[2],
                classification=list(row[3:7]),
                activity_unit=row[7],
                gas_unit=row[8],
                geography="GB",
                publication_year=2026,
                purpose=CATEGORIES[row[2]],
                methodology_reference=(
                    "methodology sections 12.22-12.27" if row[2] == "Waste disposal" else None
                ),
                transport_embedded=(True if row[2] == "Waste disposal" else None),
            )
        )
    if not records:
        raise ValueError("No reviewed DEFRA categories found")
    return records


def extract_epa(rows, source_id):
    """Keep waste Table 9 units, material names and NA values verbatim."""
    expected = (
        "Material",
        "RecycledA",
        "LandfilledB",
        "CombustedC",
        "CompostedD",
        "Anaerobically Digested (Dry Digestate with Curing)",
        "Anaerobically Digested (Wet  Digestate with Curing)",
    )
    if (
        len(rows) < 499
        or tuple(rows[434][2:9]) != expected
        or rows[433][3] != "Metric Tons CO2e / Short Ton Material"
    ):
        raise ValueError("EPA Table 9 changed; review the workbook")
    records, materials = [], set()
    for row_no, row in enumerate(rows[435:496], 436):
        material = row[2]
        if not isinstance(material, str) or not material or material in materials:
            raise ValueError("Missing or duplicate EPA material")
        materials.add(material)
        for col, method in enumerate(expected[1:], 3):
            records.append(
                record(
                    source_id,
                    f"Emission Factors Hub!{chr(65 + col)}{row_no}",
                    row[col],
                    material=material,
                    method=method,
                    activity_unit="US_short_ton",
                    gas_unit="metric_tonnes_CO2e",
                    geography="US",
                    publication_year=2025,
                    gwp_basis="IPCC_AR4",
                    transport_embedded=True,
                    avoided_emissions=False,
                    purpose="scope3_waste_reporting_not_complete_recycling_process_lca",
                    methodology_reference="Emission Factors Hub!C499 (Table 9 notes)",
                )
            )
    return records


def prepare(root, catalog):
    import openpyxl

    sources = {source["id"]: source for source in catalog["sources"]}
    # Verify every downloaded methodology and source, not only numeric worksheets.
    contents = {key: verified_content(root, source) for key, source in sources.items()}
    defra_book = openpyxl.load_workbook(
        BytesIO(contents["defra-2026-flat"]),
        data_only=True,
        read_only=True,
    )
    epa_book = openpyxl.load_workbook(
        BytesIO(contents["epa-2025-hub"]),
        data_only=True,
        read_only=True,
    )
    try:
        defra = extract_defra(list(defra_book["Factors by Category"].values), "defra-2026-flat")
        epa = extract_epa(list(epa_book["Emission Factors Hub"].values), "epa-2025-hub")
    finally:
        defra_book.close()
        epa_book.close()
    transport = catalog["reviewed_transport_reference"]
    by_id = {r["factor_id"]: r for r in defra}
    for id_key, value_key, locator_key in (
        ("factor_id", "value", "locator"),
        ("wtt_factor_id", "wtt_value", "wtt_locator"),
    ):
        selected = by_id[transport[id_key]]
        if (
            selected["factor_value"] != transport[value_key]
            or selected["activity_unit"] != "tonne.km"
            or selected["gas_unit"] != "kg CO2e"
            or selected["locator"].rsplit(":", 1)[-1] != transport[locator_key].split("!")[-1]
        ):
            raise ValueError("Reviewed transport factor does not match the source")
    # GIR values are a small, visually checked transcription, not PDF text inference.
    grid = catalog["visually_checked_grid_records"]
    for item in grid:
        if item["source_id"] not in sources:
            raise ValueError("Unknown grid evidence")
        if Decimal(item["kg_co2e_per_kwh"]) != Decimal(item["t_co2e_per_mwh"]):
            raise ValueError("t/MWh -> kg/kWh conversion mismatch")
        if item["automatic_kr_route_ranking_admission"]:
            raise ValueError("Public grid factors cannot approve a complete route")
    records = [*defra, *epa]
    return {
        "version": "emission-factor-snapshots-v1",
        "checked_on": catalog["checked_on"],
        "sources": [{k: s[k] for k in ("id", "sha256", "bytes", "url")} for s in sources.values()],
        "counts": {
            "defra_reviewed_category_rows": len(defra),
            "defra_categories": dict(Counter(r["category"] for r in defra)),
            "epa_table9_materials": len({r["material"] for r in epa}),
            "epa_table9_method_cells": len(epa),
            "published_numeric_cells": sum(r["factor_value"] is not None for r in records),
            "missing_cells": sum(r["factor_value"] is None for r in records),
            "korean_grid_records": len(grid),
        },
        "records": records,
        "korean_grid_records": grid,
        "engine_db_updated": False,
        "automatic_kr_route_ranking_admission": False,
        "model_transfer_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/config/emission_factor_sources_v1.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.root, json.loads(args.catalog.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
