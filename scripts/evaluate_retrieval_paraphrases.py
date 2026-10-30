"""Local labelled-query probe; not a legal opinion or independent benchmark."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from resource_ai.core.dependencies import get_repository
from resource_ai.evidence.models import SearchGroup
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    path = Path("data/config/retrieval_eval_paraphrases_v1.json")
    repository, rows = get_repository(), []
    for case in load_retrieval_cases(path):
        hits = repository.search(case.query, 5, search_group=SearchGroup.LEGAL)
        rows.append(
            {
                "query": case.query,
                "expected_any": case.expected_any,
                "locators": [h.citation for h in hits],
                "passed": evaluate_case(case, hits).passed,
            }
        )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": json.loads(path.read_text(encoding="utf-8"))["scope"],
        "passed": sum(r["passed"] for r in rows),
        "total": len(rows),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
