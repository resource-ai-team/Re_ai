"""Prepare local public snapshots for review, without writing to the engine DB.

No network, model calls, permit approval, price inference or automatic KG merge.
Use bundled Python with openpyxl for the downloaded XLSX files.
"""

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REGIONS = ("수도권", "강원", "충북", "충남", "전북", "전남", "경북", "경남")
NAMES = {"업소명", "처리업체명", "업체명"}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verified_bytes(root, source):
    path = root / source["file"]
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
        raise ValueError(f"Snapshot hash mismatch: {source['id']}")
    if len(content) != manifest["bytes"]:
        raise ValueError(f"Snapshot length mismatch: {source['id']}")
    return content, manifest


def csv_rows(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp949")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("Missing or duplicate CSV headers")
    rows = list(reader)
    if not rows or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError("Empty CSV or inconsistent columns")
    return rows


def prepare_prices(rows, source_id):
    expected = {"시점", "구분", "데이터 기준일", *REGIONS}
    if any(set(row) != expected for row in rows):
        raise ValueError("Price columns changed; review the source schema")
    observations, keys = [], set()
    for row_no, row in enumerate(rows, 2):
        for region in REGIONS:
            key = (row["시점"], row["구분"], region)
            if key in keys:
                raise ValueError("Duplicate item/observation-date/region price")
            keys.add(key)
            raw = row[region].strip()
            value = None if not raw else Decimal(raw)
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError("Invalid survey price")
            observations.append(
                {
                    "source_id": source_id,
                    "locator": f"CSV row {row_no}, column {region}",
                    "item_raw": row["구분"],
                    "observed_on": row["시점"],
                    "published_on": row["데이터 기준일"],
                    "region": region,
                    "price_krw_per_kg": str(value) if value is not None else None,
                    "status": "published" if value is not None else "not_published",
                    "price_basis": "regional_survey_average_not_transaction_quote",
                    "vat_included": None,
                    "logistics_included": None,
                    "operational_quote_verified": False,
                    "automatic_comparison_admission": False,
                }
            )
    return observations


def compact(value):
    return re.sub(r"\s+", "", str(value or ""))


def extract_sheet(rows, source, sheet):
    headers = next(
        ((i, row) for i, row in enumerate(rows[:8]) if any(compact(v) in NAMES for v in row)),
        None,
    )
    if headers is None:
        raise ValueError(f"No recognized facility header in {source['id']}/{sheet}")
    header_index, columns = headers
    name_col = next(i for i, v in enumerate(columns) if compact(v) in NAMES)
    result = []
    for row_no, row in enumerate(rows[header_index + 1 :], header_index + 2):
        if name_col >= len(row) or not row[name_col]:
            continue
        cells = {
            compact(column): str(value).strip() if value is not None else None
            for column, value in zip(columns, row)
            if compact(column) and "전화" not in compact(column)
        }
        capacity = {
            column: value
            for column, value in cells.items()
            if any(token in column for token in ("처리능력", "처리용량"))
        }
        result.append(
            {
                "source_id": source["id"],
                "source_as_of": source["as_of"],
                "locator": {"sheet": sheet, "row": row_no},
                "cells": cells,
                "record_role": "container_manufacturer" if "전용용기" in sheet else "listing",
                "address_is_office": any("사무실" in key for key in cells),
                "published_capacity_raw": capacity or None,
                "current_spare_capacity": None,
                "permit_original_verified": False,
                "live_lot_acceptance_verified": False,
                "national_registry_identity_verified": False,
                "model_transfer_allowed": False,
            }
        )
    return result


def prepare(root, output, catalog):
    if output.exists():
        raise ValueError("Choose a new output directory; snapshots are not overwritten")
    inventory, prices, facilities, municipal = [], [], [], []
    for source in catalog["sources"]:
        content, manifest = verified_bytes(root, source)
        item = {
            **source,
            "sha256": manifest["sha256"],
            "bytes": len(content),
            "retrieved_at": manifest["retrieved_at"],
            "model_transfer_allowed": False,
            "automatic_engine_ingestion": False,
        }
        if source["kind"] == "market_prices":
            rows = csv_rows(content)
            prices = prepare_prices(rows, source["id"])
            item["rows"] = len(rows)
        elif source["kind"] == "municipal_registry":
            rows = csv_rows(content)
            item["rows"] = len(rows)
            for i, row in enumerate(rows, 2):
                municipal.append(
                    {
                        "source_id": source["id"],
                        "source_as_of": source["as_of"],
                        "locator": f"CSV row {i}",
                        "cells": {k: v or None for k, v in row.items() if "전화" not in k},
                        "permit_original_verified": False,
                        "live_lot_acceptance_verified": False,
                        "national_registry_identity_verified": False,
                        "model_transfer_allowed": False,
                    }
                )
        elif source["kind"] == "environmental_office_register":
            import openpyxl

            workbook = openpyxl.load_workbook(
                io.BytesIO(content), read_only=True, data_only=True, keep_links=False
            )
            sheets = []
            try:
                for sheet in workbook:
                    records = extract_sheet(
                        list(sheet.iter_rows(values_only=True)), source, sheet.title
                    )
                    facilities.extend(records)
                    sheets.append({"sheet": sheet.title, "records": len(records)})
            finally:
                workbook.close()
            item["sheets"] = sheets
            item["records"] = sum(s["records"] for s in sheets)
        inventory.append(item)
    output.mkdir(parents=True)
    write_json(output / "market-prices.json", prices)
    write_json(output / "environmental-office-registers.json", facilities)
    write_json(output / "municipal-registers.json", municipal)
    report = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "sources": inventory,
        "source_count": len(inventory),
        "price_observations": len(prices),
        "published_prices": sum(p["status"] == "published" for p in prices),
        "unpublished_prices": sum(p["status"] == "not_published" for p in prices),
        "environmental_office_records_by_source": dict(Counter(r["source_id"] for r in facilities)),
        "container_manufacturer_records": sum(
            r["record_role"] == "container_manufacturer" for r in facilities
        ),
        "capacity_cells_present_records": sum(
            any((r["published_capacity_raw"] or {}).values()) for r in facilities
        ),
        "municipal_records": len(municipal),
        "unique_operators_after_deduplication": None,
        "complete_real_transaction_gold_cases": 0,
        "production_db_writes": 0,
        "paid_api_calls": 0,
        "notes": [
            "수집 행 수는 고유 시설 수가 아니며 기존 전국 명부와 중복될 수 있다.",
            "전용용기 제조업 8행은 처리업체 추천 대상에서 제외해야 한다.",
            "평균가격·예정단가·공시 용량을 실제 견적·여유 용량으로 바꾸지 않는다.",
            "시점·소재·형태·경계가 다른 자료를 한 로트의 실제 사례처럼 합치지 않는다.",
        ],
    }
    write_json(output / "inventory.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/config/verification_sources_v1.json")
    )
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    report = prepare(args.input, args.output, catalog)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "sources"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
