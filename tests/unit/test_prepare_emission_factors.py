"""Factor source integrity and accounting-boundary regression checks (offline)."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.prepare_emission_factors import (
    DEFRA_HEADERS,
    extract_defra,
    extract_epa,
    numeric,
    verified_content,
)


@pytest.mark.parametrize("value", [None, "", "NA", "N/A"])
def test_unavailable_factor_is_unknown(value):
    assert numeric(value) is None


def test_reported_zero_and_signed_values_keep_their_meaning():
    assert numeric(0) == "0"
    assert numeric(-0.5) == "-0.5"  # Some LCA conventions use negative values.
    with pytest.raises(TypeError):
        numeric(True)
    with pytest.raises(ValueError):
        numeric("NaN")


def defra_fixture():
    return [
        *[tuple([None] * 10) for _ in range(5)],
        DEFRA_HEADERS,
        (
            "20_505_5413_15_1",
            "Scope 3",
            "Waste disposal",
            "Plastic",
            "PET",
            None,
            "Re-use",
            "tonnes",
            "kg CO2e",
            None,
        ),
        (
            "20_505_5415_15_1",
            "Scope 3",
            "Waste disposal",
            "Plastic",
            "PET",
            None,
            "Closed-loop",
            "tonnes",
            "kg CO2e",
            4.65358,
        ),
    ]


def test_scope3_waste_number_is_not_admitted_as_route_lca():
    missing, available = extract_defra(defra_fixture(), "synthetic-fixture")
    assert missing["factor_value"] is None
    assert available["factor_value"] == "4.65358"
    assert available["activity_unit"] == "tonnes"  # NOT kg
    assert available["transport_embedded"]
    assert "not_route_lca" in available["purpose"]
    assert not available["automatic_kr_route_ranking_admission"]
    assert not available["model_transfer_enabled"]


def test_revised_workbook_schema_and_duplicate_ids_fail_closed():
    rows = defra_fixture()
    rows[5] = (*DEFRA_HEADERS[:-1], "GHG Conversion Factor 2025")
    with pytest.raises(ValueError, match="columns changed"):
        extract_defra(rows, "fixture")
    rows = defra_fixture()
    rows.append(rows[-1])
    with pytest.raises(ValueError, match="Duplicate"):
        extract_defra(rows, "fixture")


def epa_fixture():
    rows = [[None] * 28 for _ in range(499)]
    rows[433][3] = "Metric Tons CO2e / Short Ton Material"
    rows[434][2:9] = [
        "Material",
        "RecycledA",
        "LandfilledB",
        "CombustedC",
        "CompostedD",
        "Anaerobically Digested (Dry Digestate with Curing)",
        "Anaerobically Digested (Wet  Digestate with Curing)",
    ]
    for i in range(435, 496):
        rows[i][2:9] = [f"fixture-{i}", 0.23, 0.02, 2.05, "NA", None, 0]
    return rows


def test_epa_short_tons_gwp_and_missing_methods_are_preserved():
    result = extract_epa(epa_fixture(), "fixture")
    assert result[0]["factor_value"] == "0.23"
    assert result[0]["activity_unit"] == "US_short_ton"
    assert result[0]["gas_unit"] == "metric_tonnes_CO2e"
    assert result[0]["gwp_basis"] == "IPCC_AR4"
    assert result[0]["transport_embedded"]
    assert not result[0]["avoided_emissions"]
    assert result[3]["factor_value"] is None
    assert result[4]["factor_value"] is None
    assert result[5]["factor_value"] == "0"


def test_epa_unit_change_cannot_silently_make_values_1000_times_wrong():
    rows = epa_fixture()
    rows[433][3] = "kg CO2e / metric tonne"
    with pytest.raises(ValueError, match="Table 9 changed"):
        extract_epa(rows, "fixture")


def test_snapshot_tampering_and_truncation_stop_preparation(tmp_path):
    path = tmp_path / "source.xlsx"
    content = b"synthetic fixture"
    path.write_bytes(content)
    source = {
        "id": "fixture",
        "file": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    assert verified_content(tmp_path, source) == content
    with pytest.raises(ValueError, match="length"):
        verified_content(tmp_path, {**source, "bytes": len(content) + 1})
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        verified_content(tmp_path, source)


def test_korean_grid_catalog_keeps_year_method_and_component_scope():
    catalog = json.loads(Path("data/config/emission_factor_sources_v1.json").read_text("utf-8"))
    single, average = catalog["visually_checked_grid_records"]
    assert single["reference_years"] == [2023]
    assert single["approval_year"] == 2025
    assert single["kg_co2e_per_kwh"] == single["t_co2e_per_mwh"] == "0.4173"
    assert single["gwp_basis"] == "IPCC_AR5"
    assert average["reference_years"] == [2021, 2022, 2023]
    assert average["kg_co2e_per_kwh"] == "0.4330"
    assert all(
        not r["automatic_kr_route_ranking_admission"]
        for r in catalog["visually_checked_grid_records"]
    )
