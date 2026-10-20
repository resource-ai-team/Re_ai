"""Reproduce the pinned Annex 4 search dictionary; requires optional pdfplumber."""

import argparse
import hashlib
import json
import re
from pathlib import Path

PDF_SHA256 = "9ccd6593f4e93a216594b634e204d091d42b174d7cf8d69e4fe502865d492023"
MATERIALS = {}
for code in ["51-03-01", "51-03-06", "51-03-07", "51-03-99", "91-06-01", "91-21-02"]:
    MATERIALS[code] = ["pet", "pp", "pe"]
for code in ["51-28-01", "51-28-02", "51-28-03", "91-04-00", "91-21-01"]:
    MATERIALS[code] = ["paper"]
for code in ["51-29-02", "51-29-03", "51-29-04", "51-29-99", "91-05-00", "91-21-03"]:
    MATERIALS[code] = ["aluminium"]
NAMED = {"01-01-01": ["pe"], "01-01-02": ["pp"], "01-01-04": ["pet"]}


def extract(path):
    import pdfplumber

    if hashlib.sha256(path.read_bytes()).hexdigest() != PDF_SHA256:
        raise ValueError("Annex PDF changed; review the new law version before updating hints")
    rows = []
    with pdfplumber.open(path) as document:
        for page_number, page in enumerate(document.pages, 1):
            for line in (page.extract_text() or "").splitlines():
                match = re.match(r"^(\d{2}-\d{2}-\d{2})\s+(.+)", line)
                if not match:
                    continue
                code, label = match.groups()
                rows.append(
                    {
                        "code": code,
                        "label_excerpt": label,
                        "page": page_number,
                        "section": "business_non_designated"
                        if code.startswith("51-")
                        else "household"
                        if code.startswith("91-")
                        else "designated",
                        "query_materials": NAMED.get(code, MATERIALS.get(code, [])),
                        "match_kind": "named_material_code_only"
                        if code in NAMED
                        else "code_category_only"
                        if code in MATERIALS
                        else None,
                    }
                )
    if len(rows) != 313 or len({r["code"] for r in rows}) != 313:
        raise ValueError("Unexpected Annex extraction; inspect the original table")
    return {
        "schema_version": 1,
        "scope": "search_hints_only_not_lot_classification_permit_or_process_approval",
        "source": {
            "title": "폐기물관리법 시행규칙 별표 4: 폐기물의 종류별 세부분류",
            "url": "https://www.law.go.kr/법령별표서식/(폐기물관리법 시행규칙,20261002,별표4)",
            "pdf_url": "https://www.law.go.kr/LSW/flDownload.do?flSeq=170401309",
            "pdf_sha256": PDF_SHA256,
            "law_effective_on": "2026-10-02",
            "annex_amended_on": "2025-12-30",
            "verified_on": "2026-10-21",
            "extraction": "exact six-digit row headers with first-line excerpts; not full legal definitions",
            "review": "query hints manually reviewed on PDF pages 1, 6, 10, 11, 13, 14",
        },
        "records": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.input)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"Extracted {len(result['records'])} code headers from pinned official PDF")
