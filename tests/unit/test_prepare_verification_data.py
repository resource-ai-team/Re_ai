"""Public snapshot checks, not a Critic quality benchmark."""

import hashlib
import json

import pytest

from scripts.prepare_verification_data import (
    REGIONS,
    csv_rows,
    extract_sheet,
    prepare_prices,
    verified_bytes,
)


def price_row():
    return {
        "시점": "2026-10-10",
        "구분": "골판지",
        "데이터 기준일": "2026-10-26",
        **dict.fromkeys(REGIONS, ""),
        "수도권": "103.7",
        "충북": "0",
    }


def test_unpublished_price_is_not_zero_or_a_customer_quote():
    result = prepare_prices([price_row()], "synthetic-parser-fixture")
    assert len(result) == 8
    assert result[0]["price_krw_per_kg"] == "103.7"
    assert result[1]["price_krw_per_kg"] is None
    assert result[1]["status"] == "not_published"
    assert result[2]["price_krw_per_kg"] == "0"
    assert all(not r["operational_quote_verified"] for r in result)
    assert all(not r["automatic_comparison_admission"] for r in result)
    assert result[0]["observed_on"] != result[0]["published_on"]


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1", "10원"])
def test_malformed_price_stops_preparation(invalid):
    row = price_row()
    row["수도권"] = invalid
    with pytest.raises((ValueError, ArithmeticError)):
        prepare_prices([row], "synthetic-parser-fixture")


def test_duplicate_market_key_and_changed_schema_are_not_silently_accepted():
    row = price_row()
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_prices([row, row], "fixture")
    del row["경남"]
    with pytest.raises(ValueError, match="columns changed"):
        prepare_prices([row], "fixture")


@pytest.mark.parametrize("content", [b"a,a\n1,2\n", b"a,b\n1\n", b"a,b\n1,2,3\n"])
def test_ambiguous_csv_structure_is_rejected(content):
    with pytest.raises(ValueError):
        csv_rows(content)


def test_csv_supports_cp949_without_losing_korean_labels():
    assert csv_rows("품목,가격\n골판지,103.7\n".encode("cp949"))[0]["품목"] == "골판지"


def test_office_and_published_capacity_never_become_live_operational_facts():
    result = extract_sheet(
        [
            ["공개 명부", None, None, None],
            ["업소명", "사무실소재지", "1일 처리능력", "전화번호"],
            ["가상업체", "가상 사무실", "28㎥/일", "synthetic-contact"],
        ],
        {"id": "fixture", "as_of": "2026-01"},
        "재활용업",
    )[0]
    assert result["address_is_office"]
    assert result["published_capacity_raw"] == {"1일처리능력": "28㎥/일"}
    assert result["current_spare_capacity"] is None
    assert not result["permit_original_verified"]
    assert not result["live_lot_acceptance_verified"]
    assert not result["national_registry_identity_verified"]
    assert "전화번호" not in result["cells"]
    assert result["locator"] == {"sheet": "재활용업", "row": 3}


def test_container_manufacturer_is_kept_distinct_from_waste_processor():
    result = extract_sheet(
        [["업체명"], ["가상 전용용기 제조사"]],
        {"id": "fixture", "as_of": "2026-01"},
        "전용용기 제조업",
    )[0]
    assert result["record_role"] == "container_manufacturer"
    assert result["published_capacity_raw"] is None
    assert not result["model_transfer_allowed"]


def test_changed_snapshot_fails_before_using_any_values(tmp_path):
    content = b"original public fixture"
    (tmp_path / "fixture.csv").write_bytes(content)
    (tmp_path / "fixture.manifest.json").write_text(
        json.dumps({"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    )
    source = {"id": "fixture", "file": "fixture.csv"}
    assert verified_bytes(tmp_path, source)[0] == content
    (tmp_path / "fixture.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verified_bytes(tmp_path, source)
