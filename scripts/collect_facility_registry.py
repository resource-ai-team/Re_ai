"""Collect the public national facility download, within the portal's 50k limit."""

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import httpx

from resource_ai.knowledge_graph.facility_registry import MAX_BYTES, NATIONAL_COLUMNS, SOURCES

ROOT = "https://www.data.go.kr"


def collect(client, output):
    if output.exists() or output.with_suffix(".manifest.json").exists():
        raise ValueError("Choose a new snapshot path; existing files are not overwritten")
    header = client.get(ROOT + "/download/columList.json", params={"pk": "15114147", "ext": "JSON"})
    header.raise_for_status()
    data = header.json()
    count, table = data["totalCount"], data["tableVO"]
    columns = table["colNmList"]
    if (
        type(count) is not int
        or not 1 <= count <= 50000
        or table["svcTableNm"] != "tn_pubr_public_tret_was_svc"
        or not isinstance(columns, list)
        or set(columns) != NATIONAL_COLUMNS - {"INSTT_CODE", "INSTT_NM"}
    ):
        raise ValueError("Portal schema/count changed; use the official download/API and review it")
    records, pages = [], []
    for page in range(1, math.ceil(count / 10000) + 1):
        params = {
            "publicDataPk": "15114147",
            "totalCount": count,
            "svcTableNm": table["svcTableNm"],
            "perPage": 10000,
            "page": page,
            "colNmList": columns,
        }
        response = client.get(ROOT + "/download/standard.json", params=params)
        response.raise_for_status()
        rows = response.json()
        if (
            not isinstance(rows, list)
            or not rows
            or len(response.content) > MAX_BYTES
            or any(not isinstance(row, dict) or set(row) != NATIONAL_COLUMNS for row in rows)
        ):
            raise ValueError("Incomplete or changed public download response")
        records.extend(rows)
        pages.append(
            {
                "page": page,
                "rows": len(rows),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
        )
    if (
        len(records) != count
        or len({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records}) != count
    ):
        raise ValueError("Download count mismatch or repeated pages; no snapshot written")
    content = (
        response.content if len(pages) == 1 else json.dumps(records, ensure_ascii=False).encode()
    )
    if len(content) > MAX_BYTES:
        raise ValueError("Collected snapshot exceeds byte limit")
    manifest = {
        "source_url": SOURCES["national-json"],
        "format": "national-json",
        "rows": count,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "pages": pages,
        "model_transfer_allowed": False,
        "license_note": "Review provider-level terms before public redistribution",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            result = collect(client, args.output)
    except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError):
        parser.exit(
            1,
            "공개 다운로드 응답·행 수·출력 경로를 확인해 주세요. 기존 파일을 덮어쓰지 않습니다.\n",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
