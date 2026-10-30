"""Compare local candidate pools/rerankers. Developer labels are not expert judgments."""

import argparse
import json
import os
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from resource_ai.core.dependencies import get_repository
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases, summarize_by_group
from resource_ai.rag.retrieval.cross_encoder import OnnxCrossEncoder
from resource_ai.rag.retrieval.diversity import distinct_articles
from resource_ai.rag.retrieval.reranker import rerank_results

MODELS = {
    "bge": (
        "models/bge-reranker-onnx-int8",
        "onnx/model_int8.onnx",
        "onnx-community/bge-reranker-v2-m3-ONNX@6f5ff65298512715a1e669753bc754d2bc8f367b",
    ),
    "minilm": (
        "models/mmarco-reranker-onnx-int8",
        "onnx/model_quint8_avx2.onnx",
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1@1427fd652930e4ba29e8149678df786c240d8825",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "test", "all"], required=True)
    parser.add_argument("--dataset", type=Path, default=Path("data/config/retrieval_study_v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["rules20", "rules50", "rules100", "bge50", "bge100", "minilm50", "minilm100"],
    )
    args = parser.parse_args()
    if any(not re.fullmatch(r"(rules|bge|minilm)(20|50|100)d?", p) for p in args.profiles):
        parser.error("Unknown profile")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch

    torch.set_num_threads(4)
    path = args.dataset
    raw = json.loads(path.read_text(encoding="utf8"))
    split_ids = {r["id"] for r in raw["cases"] if r.get("split") == args.split}
    cases = [c for c in load_retrieval_cases(path) if args.split == "all" or c.case_id in split_ids]
    models = {
        name: OnnxCrossEncoder(Path(folder), weight, model_id=identity)
        for name, (folder, weight, identity) in MODELS.items()
        if any(p.startswith(name) for p in args.profiles)
    }
    repository, reports, evaluations = get_repository(), [], {p: [] for p in args.profiles}
    for case in cases:
        started = time.perf_counter()
        pools = {}
        for count in sorted(
            {
                int(
                    p.removeprefix("rules")
                    .removeprefix("bge")
                    .removeprefix("minilm")
                    .removesuffix("d")
                )
                for p in args.profiles
            }
        ):
            repository.candidate_minimum = count
            pools[count] = repository.retrieve_candidates(
                case.query, 5, search_group=case.search_group
            )
        union = {h.chunk_id: h for pool in pools.values() for h in pool}
        # Pools are nested for the production two-lane fusion; preserve the safety cap if not.
        scores, timings = {}, {}
        for name, model in models.items():
            start = time.perf_counter()
            keys = list(union)
            values = []
            for offset in range(0, len(keys), 100):
                values.extend(
                    model.scores(case.query, [union[k] for k in keys[offset : offset + 100]])
                )
            scores[name] = dict(zip(keys, values, strict=True))
            timings[name] = round(time.perf_counter() - start, 3)
        row = {
            "id": case.case_id,
            "group": case.search_group,
            "profiles": {},
            "model_seconds_for_union": timings,
        }
        for profile in args.profiles:
            name = (
                "rules"
                if profile.startswith("rules")
                else "bge"
                if profile.startswith("bge")
                else "minilm"
            )
            count = int(profile.removeprefix(name).removesuffix("d"))
            pool = pools[count]
            ranked = (
                rerank_results(case.query, pool, limit=100, search_group=case.search_group)
                if name == "rules"
                else sorted(pool, key=lambda h: (-scores[name][h.chunk_id], -h.score, h.chunk_id))
            )
            ranked = distinct_articles(ranked, 5) if profile.endswith("d") else ranked[:5]
            result = evaluate_case(case, ranked)
            evaluations[profile].append(result)
            row["profiles"][profile] = {
                "hit_at_5": result.passed,
                "rank": result.first_relevant_rank,
                "candidate_hit": evaluate_case(case, pool).passed,
                "locators": [h.citation or h.heading for h in ranked],
            }
        row["seconds"] = round(time.perf_counter() - started, 3)
        reports.append(row)
        print(
            json.dumps(
                {
                    "completed": len(reports),
                    "total": len(cases),
                    "id": case.case_id,
                    "seconds": row["seconds"],
                }
            ),
            flush=True,
        )
        summary = {
            p: {str(g): asdict(s) for g, s in summarize_by_group(e).items()}
            for p, e in evaluations.items()
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "split": args.split,
                    "dataset_sha256": sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                    "hash_line_endings": "LF normalized",
                    "label_status": raw.get(
                        "label_status", "developer_regression_cases_not_expert_reviewed"
                    ),
                    "completed": len(reports),
                    "total": len(cases),
                    "models": {k: v[2] for k, v in MODELS.items() if k in models},
                    "max_length": 512,
                    "summary": summary,
                    "cases": reports,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf8",
        )


if __name__ == "__main__":
    main()
