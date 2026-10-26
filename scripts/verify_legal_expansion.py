"""실제 E5/pgvector 적재 수·출처·검색을 확인한다. 법률 적용성 평가는 아니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from resource_ai.core.dependencies import get_repository
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases, summarize_by_group
from resource_ai.rag.ingestion.dataset import verify_dataset_manifest
from resource_ai.rag.ingestion.loader import load_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/official/legal-expansion-2026-10-26/documents.jsonl"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    manifest = verify_dataset_manifest(args.dataset.with_suffix(".manifest.json"), args.dataset)
    docs, repository = load_jsonl(args.dataset), get_repository()
    source_ids = [d.source_id for d in docs]
    with repository.database.connect() as connection:
        stored = connection.execute(
            """
            SELECT d.source_id,count(c.id),min(vector_dims(c.embedding)),
              max(vector_dims(c.embedding)),
              bool_and(c.metadata->>'embedding_recipe'='document-heading-content-v1'),
              d.metadata->>'embedding_model', d.metadata->>'raw_xml_sha256'
            FROM legal_documents d JOIN legal_chunks c ON c.document_id=d.id
            WHERE d.source_id=ANY(%s) AND d.is_current AND d.tenant_id IS NULL
              AND d.document_type='LAW' AND d.effective_from<=CURRENT_DATE
            GROUP BY d.id ORDER BY d.source_id
        """,
            (source_ids,),
        ).fetchall()
        current_counts = connection.execute("""
            SELECT d.document_type,count(DISTINCT d.id),count(c.id)
            FROM legal_documents d LEFT JOIN legal_chunks c ON c.document_id=d.id
            WHERE d.is_current GROUP BY d.document_type ORDER BY d.document_type
        """).fetchall()
        duplicates = connection.execute("""
            SELECT metadata->>'canonical_source_id',count(*) FROM legal_documents
            WHERE document_type='LAW' AND is_current AND tenant_id IS NULL
            GROUP BY metadata->>'canonical_source_id' HAVING count(*)>1
        """).fetchall()
    stored_ids = {row[0] for row in stored}
    expected_hashes = {d.source_id: d.metadata["raw_xml_sha256"] for d in docs}
    cache_checks = []
    for d in docs:
        key = (
            f"eflaw:{d.metadata['selected_mst']}:{d.metadata['effective_date']}"
            if d.metadata["legal_source_kind"] == "statute"
            else f"admrul:{d.metadata['administrative_serial']}"
        )
        cache = args.dataset.parent / "xml" / (hashlib.sha256(key.encode()).hexdigest() + ".xml")
        cache_checks.append(
            cache.exists()
            and hashlib.sha256(cache.read_bytes()).hexdigest() == expected_hashes[d.source_id]
        )
    suites = []
    for name, path in [
        ("legacy_regression", Path("data/config/retrieval_eval.json")),
        ("legacy_paraphrases", Path("data/config/retrieval_eval_paraphrases_v1.json")),
        ("new_source_probes", Path("data/config/legal_expansion_eval.json")),
    ]:
        rows, evaluations = [], []
        for case in load_retrieval_cases(path):
            hits = repository.search(case.query, 5, search_group=case.search_group)
            evaluation = evaluate_case(case, hits)
            evaluations.append(evaluation)
            rows.append(
                {
                    **asdict(evaluation),
                    "expected_any": case.expected_any,
                    "locators": [h.citation for h in hits],
                }
            )
        suites.append(
            {
                "name": name,
                "dataset": path.as_posix(),
                "by_group": {k: asdict(v) for k, v in summarize_by_group(evaluations).items()},
                "cases": rows,
            }
        )
    missing_titles = [
        "비료관리법",
        "산업입지 및 개발에 관한 법률",
        "수도법",
        "에너지법",
        "자동차관리법",
        "해양폐기물 및 해양오염퇴적물 관리법",
        "환경정책기본법",
    ]
    coverage = {
        "dataset_sources": len(docs),
        "stored_sources": len(stored),
        "stored_chunks": sum(row[1] for row in stored),
        "all_sources_present": set(source_ids) == stored_ids,
        "same_embedding_recipe": all(
            r[2:6] == (384, 384, True, "intfloat/multilingual-e5-small") for r in stored
        ),
        "duplicate_current_canonical_sources": duplicates,
        "all_xml_cache_hashes_verified": all(cache_checks),
        "stored_source_hashes_match_dataset": all(r[6] == expected_hashes[r[0]] for r in stored),
        "missing_cross_reference_documents_present": all(
            any(d.title == title and d.source_id in stored_ids for d in docs)
            for title in missing_titles
        ),
    }
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": "2026-10-26",
        "scope": "Local real DB and local E5; developer-authored retrieval probes, not expert legal validation",
        "live_model_api_calls": 0,
        "dataset_sha256": manifest["dataset_sha256"],
        "coverage": coverage,
        "current_corpus_counts": current_counts,
        "suites": suites,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    inventory = {
        "as_of": "2026-10-26",
        "dataset_sha256": manifest["dataset_sha256"],
        "scope": "Official source metadata only; raw XML, PDF text and vectors are local",
        "source_count": len(docs),
        "sources": [
            {
                "source_id": d.source_id,
                "title": d.title,
                "source_url": d.source_url,
                "version": d.version,
                "effective_from": str(d.effective_from),
                "kind": d.metadata["legal_source_kind"],
                "topic": d.metadata["topic"],
                "sections": len(d.sections),
                "raw_xml_sha256": d.metadata["raw_xml_sha256"],
                "annex_count": d.metadata["annex_count"],
                "annex_text_missing": d.metadata["annex_text_missing"],
                "images_in_xml": d.metadata["images_in_xml"],
                "supplementary_provisions_indexed": False,
                "attachment_count": len(d.metadata.get("attachments", [])),
                "attachment_pdf_files": len(d.metadata.get("attachment_files", [])),
                "attachment_empty_pages": d.metadata.get("attachment_empty_pages", []),
                "coverage": d.metadata["coverage"],
            }
            for d in docs
        ],
    }
    args.output.with_suffix(".sources.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(coverage, ensure_ascii=False))
    for suite in suites:
        print(
            suite["name"], {k: f"{v['passed']}/{v['total']}" for k, v in suite["by_group"].items()}
        )
    return (
        0
        if (
            coverage["all_sources_present"]
            and coverage["same_embedding_recipe"]
            and coverage["all_xml_cache_hashes_verified"]
            and coverage["stored_source_hashes_match_dataset"]
            and not duplicates
            and coverage["missing_cross_reference_documents_present"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
