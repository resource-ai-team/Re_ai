"""Read an acquired KECO CSV with exact period, region and grade matching.

No network fetch on analysis. Missing observations stay null; averages are not quotes.
"""

import csv
import hashlib
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path

REGIONS = {"수도권", "강원", "충북", "충남", "전북", "전남", "경북", "경남"}


def load_snapshot(path, manifest):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return {"status": "raw_csv_not_acquired", "records": []}
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != manifest.get("sha256"):
        return {"status": "snapshot_hash_mismatch", "records": []}
    try:
        text = content.decode(manifest.get("encoding", "cp949"))
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or not all(
            {"시점", "구분", "데이터 기준일", *REGIONS} <= row.keys() for row in rows
        ):
            raise ValueError("invalid schema")
        records = []
        for row in rows:
            if row["시점"][:7] != manifest["period"]:
                raise ValueError("snapshot period mismatch")
            for region in sorted(REGIONS):
                raw = row[region].strip()
                value = Decimal(raw.replace(",", "")) if raw else None
                if value is not None and (not value.is_finite() or value < 0):
                    raise ValueError("invalid price")
                records.append(
                    {
                        "period": row["시점"][:7],
                        "observed_on": row["시점"],
                        "published_on": row["데이터 기준일"],
                        "item": row["구분"],
                        "region": region,
                        "price_krw_per_kg": str(value) if value is not None else None,
                        "status": "published" if value is not None else "not_published",
                        "source_url": manifest.get("source_url"),
                        "sha256": checksum,
                        "license": manifest.get("license"),
                        "is_quote": False,
                    }
                )
    except (UnicodeError, LookupError, KeyError, ValueError, InvalidOperation):
        return {"status": "invalid_snapshot", "records": []}
    return {"status": "loaded", "records": records, "sha256": checksum}


def select_price(snapshot, *, period, region, item):
    if snapshot["status"] != "loaded":
        return {"status": snapshot["status"], "price_krw_per_kg": None}
    for record in snapshot["records"]:
        if (record["period"], record["region"], record["item"]) == (period, region, item):
            return record
    return {"status": "period_region_item_mismatch", "price_krw_per_kg": None}
