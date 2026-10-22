"""Local public registry -> separate, immutable Neo4j listing generations.

These are source records, not resolved facility identities or capabilities. No
registry data is shipped with the package, sent to a model or silently read at runtime.
"""

import argparse
import csv
import io
import json
import math
import re
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from neo4j.exceptions import DriverError, Neo4jError

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph

OWNER = "re-source-facility-registry-v1"
MAX_ROWS = 100000
MAX_BYTES = 50 * 1024 * 1024
SOURCES = {
    "national-json": "https://www.data.go.kr/data/15114147/standard.do",
    "gimhae-csv": "https://www.data.go.kr/data/15033433/fileData.do",
}
NATIONAL_COLUMNS = {
    "FLCT_NM",
    "LCTN_ROAD_NM_ADDR",
    "LCTN_LOTNO_ADDR",
    "LAT",
    "LOT",
    "BUS_NM",
    "SPE_TRET_AREA_NM",
    "TRET_WAS_INF",
    "BUS_AREA",
    "FLCT_EQU_NM",
    "PERMI_YMD",
    "TELNO",
    "MNG_INST_NM",
    "CRTR_YMD",
    "INSTT_CODE",
    "INSTT_NM",
}
GIMHAE_COLUMNS = {"연번", "업종", "업소명", "등록일자", "폐기물종류", "주소", "위도", "경도"}


def text(row, name):
    return (row.get(name) or "").strip()


def coordinates(lat, lon):
    try:
        point = float(lat), float(lon)
        if (
            all(math.isfinite(v) for v in point)
            and 32 <= point[0] <= 39.5
            and 124 <= point[1] <= 132
        ):
            return point
    except (TypeError, ValueError):
        pass
    return None


def normalize_record(row, index, format_name, source_sha):
    columns = NATIONAL_COLUMNS if format_name == "national-json" else GIMHAE_COLUMNS
    if (
        not isinstance(row, dict)
        or set(row) != columns
        or any(v is not None and not isinstance(v, str) for v in row.values())
        or len(graph.canonical(row).encode()) > 32768
    ):
        raise ValueError("Unsupported facility registry columns or values")
    if format_name == "national-json":
        name, business, waste = (text(row, k) for k in ("FLCT_NM", "BUS_NM", "TRET_WAS_INF"))
        address = text(row, "LCTN_ROAD_NM_ADDR") or text(row, "LCTN_LOTNO_ADDR")
        point = coordinates(row.get("LAT"), row.get("LOT"))
        as_of, permit_date = text(row, "CRTR_YMD"), text(row, "PERMI_YMD")
        equipment, authority = text(row, "FLCT_EQU_NM"), text(row, "MNG_INST_NM")
    else:
        name, business, waste = (text(row, k) for k in ("업소명", "업종", "폐기물종류"))
        address = text(row, "주소")
        point = coordinates(row.get("위도"), row.get("경도"))
        # This CSV has no per-row reference date. A registration date is not one.
        as_of, permit_date = "", text(row, "등록일자")
        equipment, authority = "", "경상남도 김해시"
    if not name or not business:
        raise ValueError("Facility registry name and business type are required")
    try:
        date.fromisoformat(as_of)
    except ValueError:
        parsed_date = None
    else:
        parsed_date = as_of
    return {
        "listing_id": graph.identity("facility-listing", [SOURCES[format_name], source_sha, index]),
        "source_url": SOURCES[format_name],
        "source_sha256": source_sha,
        "source_row": index,
        "record_sha256": graph.digest(row),
        "raw_record": row,
        "name": name,
        "business_type": business,
        "waste_text": waste,
        "address": address,
        "equipment_text": equipment,
        "authority": authority,
        "as_of_raw": as_of,
        "as_of": parsed_date,
        "permit_date_raw": permit_date,
        "coordinates": list(point) if point else None,
        "coordinate_quality": "parsed-korea-bbox-not-site-verified"
        if point
        else "unavailable-or-invalid",
        "is_recycling_registration": "재활용업" in business,
        "waste_codes": sorted(set(re.findall(r"(?<!\d)\d{2}-\d{2}-\d{2}(?!\d)", waste))),
        "current_acceptance": None,
        "current_available_capacity_kg": None,
        "current_permit_scope": None,
        "process_capability_verified": False,
        "model_transfer_allowed": False,
        "dispatch_approved": False,
    }


def load_snapshot(path, format_name, expected_sha256):
    if format_name not in SOURCES or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("Known registry format and expected SHA-256 are required")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Facility registry exceeds byte limit")
    content = path.read_bytes()
    if sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Facility registry source checksum mismatch")
    if format_name == "national-json":
        rows = json.loads(content.decode("utf-8-sig"))
    else:
        rows = list(csv.DictReader(io.StringIO(content.decode("cp949"))))
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError("Facility registry must be a nonempty, bounded record list")
    records = [
        normalize_record(row, i, format_name, expected_sha256) for i, row in enumerate(rows, 1)
    ]
    return records, {
        "owner": OWNER,
        "schema_version": 1,
        "format": format_name,
        "source_url": SOURCES[format_name],
        "source_sha256": expected_sha256,
        "records_sha256": graph.digest(records),
        "record_count": len(records),
        "model_transfer_allowed": False,
    }


def node_fields(record, config, generation):
    return {
        "space": config.namespace,
        "generation": generation,
        "owner": OWNER,
        "listing_id": record["listing_id"],
        "record_json": graph.canonical(record),
        "record_sha256": graph.digest(record),
    }


def registry_error(
    message="시설 명부의 버전·원본·레코드 검사가 실패했습니다. 명부를 다시 적재해 주세요.",
):
    return store.GraphStoreError("registry-integrity", message)


def _read_registry(tx, config, generation=None):
    """Audit a whole registry snapshot; absence is explicit, never a file fallback."""
    if generation is None:
        head = tx.run(
            "MATCH (h:ReSourceFacilityRegistry {space:$space}) RETURN properties(h) AS head",
            space=config.namespace,
        ).single()
        if head is None:
            return None
        head = dict(head["head"])
        if head.get("owner") != OWNER or not re.fullmatch(
            r"[a-f0-9]{32}", head.get("generation", "")
        ):
            raise registry_error()
        generation = head["generation"]
        require_ready = True
    else:
        require_ready = False
    row = tx.run(
        """MATCH (d:ReSourceFacilityDataset {space:$space,generation:$generation})
        RETURN properties(d) AS fields""",
        space=config.namespace,
        generation=generation,
    ).single()
    if row is None:
        raise registry_error()
    fields = dict(row["fields"])
    manifest = json.loads(fields["manifest_json"])
    if (
        fields.get("owner") != OWNER
        or manifest.get("owner") != OWNER
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("source_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"])
        or type(manifest.get("record_count")) is not int
        or not 1 <= manifest["record_count"] <= MAX_ROWS
        or manifest.get("source_url") != SOURCES.get(manifest.get("format"))
        or manifest.get("model_transfer_allowed") is not False
        or require_ready
        and fields.get("ready") is not True
    ):
        raise registry_error()
    records = []
    for row in tx.run(
        """MATCH (n:ReSourceFacilityListing {space:$space,generation:$generation})
        RETURN properties(n) AS fields,labels(n) AS labels ORDER BY n.listing_id LIMIT $limit""",
        space=config.namespace,
        generation=generation,
        limit=MAX_ROWS + 1,
    ):
        fields = dict(row["fields"])
        record = json.loads(fields["record_json"])
        expected = normalize_record(
            record["raw_record"],
            record["source_row"],
            manifest["format"],
            manifest["source_sha256"],
        )
        if (
            record != expected
            or fields != node_fields(expected, config, generation)
            or set(row["labels"]) != {"ReSourceFacilityListing"}
        ):
            raise registry_error()
        records.append(record)
    records.sort(key=lambda r: r["source_row"])
    if (
        len(records) != manifest["record_count"]
        or [r["source_row"] for r in records] != list(range(1, len(records) + 1))
        or graph.digest(records) != manifest["records_sha256"]
    ):
        raise registry_error()
    return {"generation": generation, "manifest": manifest, "records": records}


def read_registry(tx, config, generation=None):
    try:
        return _read_registry(tx, config, generation)
    except (KeyError, ValueError, TypeError, OverflowError):
        raise registry_error() from None


def check_owner(tx, config):
    row = tx.run(
        "MATCH (h:ReSourceFacilityRegistry {space:$space}) RETURN h.owner AS owner",
        space=config.namespace,
    ).single()
    if row and row["owner"] != OWNER:
        raise registry_error(
            "해당 namespace의 시설 명부 소유 기록이 달라 기존 자료를 변경하지 않았습니다."
        )


def import_registry(path, format_name, expected_sha256, *, config=None):
    records, manifest = load_snapshot(path, format_name, expected_sha256)
    config = config or store.settings()
    generation = uuid4().hex
    try:
        driver = store.get_driver(config)
        # Read first so another owner's registry is never overwritten.
        store._read(config, lambda tx: check_owner(tx, config))
        with driver.session(database=config.database) as session:
            for label, suffix, keys in [
                ("ReSourceFacilityListing", "listing", "(n.space,n.generation,n.listing_id)"),
                ("ReSourceFacilityDataset", "dataset", "(n.space,n.generation)"),
                ("ReSourceFacilityRegistry", "head", "n.space"),
            ]:
                session.run(
                    f"CREATE CONSTRAINT resource_facility_{suffix} IF NOT EXISTS FOR (n:{label}) REQUIRE {keys} IS UNIQUE"
                ).consume()
            session.run(
                """CREATE (d:ReSourceFacilityDataset {space:$space,generation:$generation,
                owner:$owner,ready:false,manifest_json:$manifest})""",
                space=config.namespace,
                generation=generation,
                owner=OWNER,
                manifest=graph.canonical(manifest),
            ).consume()
            for offset in range(0, len(records), 500):
                session.run(
                    "UNWIND $rows AS row CREATE (n:ReSourceFacilityListing) SET n=row",
                    rows=[
                        node_fields(r, config, generation) for r in records[offset : offset + 500]
                    ],
                ).consume()
        store._read(config, lambda tx: read_registry(tx, config, generation))

        def activate(tx):
            row = tx.run(
                """MERGE (h:ReSourceFacilityRegistry {space:$space})
                ON CREATE SET h.owner=$owner
                SET h.generation=coalesce(h.generation,'')
                RETURN h.owner AS owner""",
                space=config.namespace,
                owner=OWNER,
            ).single()
            if row is None or row["owner"] != OWNER:
                raise registry_error()
            tx.run(
                """MATCH (d:ReSourceFacilityDataset {space:$space,generation:$generation})
                SET d.ready=true WITH d
                MATCH (h:ReSourceFacilityRegistry {space:$space}) SET h.generation=$generation""",
                space=config.namespace,
                generation=generation,
            ).consume()

        with driver.session(database=config.database) as session:
            session.execute_write(activate)
        return {
            **manifest,
            "generation": generation,
            "status": "activated",
            "dispatch_approved": False,
        }
    except (Neo4jError, DriverError, OSError):
        raise store.GraphStoreError(
            "unavailable", "Neo4j 시설 명부 적재에 실패했습니다. 이전 활성 명부를 유지합니다."
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--format", choices=list(SOURCES), required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = import_registry(args.input, args.format, args.expected_sha256)
    except (store.GraphStoreError, ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(
            1,
            str(error)
            if isinstance(error, store.GraphStoreError)
            else "시설 명부 파일·형식·SHA-256을 확인해 주세요.\n",
        )
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf8")
    print(encoded)


if __name__ == "__main__":
    main()
