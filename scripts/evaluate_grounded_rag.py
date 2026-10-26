"""A/B retrieval using real E5/PG. Use dev first; labels are not expert legal judgments."""

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median

from resource_ai.core.dependencies import get_repository
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases, summarize_by_group
from resource_ai.rag.retrieval.korean_lexical import RECIPE, SCORING, KoreanLegalTokenizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/config/retrieval_study_v1.json"))
    parser.add_argument("--split", choices=["dev", "test", "all"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--purpose", choices=["regression", "validation"], default="regression")
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=["legacy", "korean", "korean-diverse", "grounded", "grounded-korean"],
        default=["legacy", "grounded"],
    )
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch

    torch.set_num_threads(4)
    repository = get_repository()
    tokenizer = (
        KoreanLegalTokenizer()
        if any(p in {"korean", "korean-diverse", "grounded-korean"} for p in args.profiles)
        else None
    )
    raw = json.loads(args.dataset.read_text(encoding="utf-8"))
    selected = {c.get("id") for c in raw["cases"] if c.get("split") == args.split}
    cases = [
        c
        for c in load_retrieval_cases(args.dataset)
        if args.split == "all" or c.case_id in selected
    ]
    reports = []
    results = {p: [] for p in args.profiles}
    durations = {p: [] for p in args.profiles}
    for case in cases:
        row = {"id": case.case_id, "group": case.search_group, "profiles": {}}
        for profile in args.profiles:
            repository.korean_tokenizer = (
                tokenizer if profile in {"korean", "korean-diverse", "grounded-korean"} else None
            )
            repository.article_diversity = profile in {
                "korean-diverse",
                "grounded",
                "grounded-korean",
            }
            repository.legal_basis_enabled = profile in {"grounded", "grounded-korean"}
            started = time.perf_counter()
            hits = repository.search(case.query, 5, search_group=case.search_group)
            elapsed = time.perf_counter() - started
            durations[profile].append(elapsed)
            e = evaluate_case(case, hits)
            results[profile].append(e)
            row["profiles"][profile] = {
                "passed": e.passed,
                "rank": e.first_relevant_rank,
                "locators": [h.citation for h in hits],
                "methods": [h.retrieval_method for h in hits],
                "seconds": round(elapsed, 3),
            }
        reports.append(row)
        print(f"completed {len(reports)}/{len(cases)}: {case.case_id}", flush=True)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": args.dataset.as_posix(),
        "dataset_sha256": sha256(args.dataset.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "split": args.split,
        "label_status": raw.get("label_status", "developer_probes_not_expert"),
        "evaluation_use": args.purpose,
        "prior_test_now_diagnostic": args.dataset.name == "retrieval_study_v1.json",
        "independent_or_expert_evaluation": False,
        "split_policy": raw.get("split_policy"),
        "korean_index_recipe": RECIPE,
        "korean_scoring": SCORING,
        "lexical_statistics": "current public requested corpus; per-statement; no query cache",
        "latency_excludes_model_startup": True,
        "latency_seconds": {
            p: {
                "median": round(median(ds), 3),
                "maximum": round(max(ds), 3),
            }
            for p, ds in durations.items()
        },
        "profiles": args.profiles,
        "summary": {
            p: {str(k): asdict(v) for k, v in summarize_by_group(es).items()}
            for p, es in results.items()
        },
        "cases": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
