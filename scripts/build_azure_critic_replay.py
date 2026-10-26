"""Join verified live receipts to a code replay. Offline only; never calls Azure."""

import argparse
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from resource_ai.agents.semantic_critic import SemanticCriticService
from resource_ai.evidence.critic_models import ModelConcern
from resource_ai.knowledge_graph.ontology import digest
from scripts.run_focused_azure_critic import focused_packet


def combine(trace, before, after):
    for phase, receipt in (("before", before), ("after", after)):
        payload, records, targets = focused_packet(trace, phase)
        if (
            receipt["phase"] != phase
            or receipt["status"] != "completed"
            or receipt["rejected_findings"]
            or receipt["call"].get("http_status") != 200
            or receipt["call"].get("verified_model") != "gpt-6-astra"
            or receipt["call"].get("reasoning_effort") != "high"
            or receipt["input_sha256"] != digest(payload)
        ):
            raise ValueError("Live receipt, scope or source packet does not match")
        for raw in receipt["concerns"]:
            concern = ModelConcern.model_validate(raw)
            if SemanticCriticService._check(concern, records, targets):
                raise ValueError("Live concern failed citation/target validation")
    if before["evidence"][:2] != after["evidence"][:2]:
        raise ValueError("The KG or synthetic quote changed between calls")
    result = deepcopy(trace)
    result["schema"] = "azure-critic-revision-replay-v1"
    result["scope"] = (
        "Live Azure focused Critic -> bounded code repair -> live recheck; not a production auto loop"
    )
    result["azure_reviews"] = {"before": before, "after": after}
    result["provenance"]["gpt_calls"] = 2
    result["provenance"]["gpt_scope"] = (
        "One of three paths; KG continuity and dimensional cost, no legal/LCI raw documents"
    )
    usage = {
        k: sum(r["call"]["usage"].get(k, 0) for r in (before, after))
        for k in ("input_tokens", "output_tokens", "reasoning_tokens")
    }
    usd = (Decimal(usage["input_tokens"]) * 10 + Decimal(usage["output_tokens"]) * 50) / 1000000
    result["live_usage"] = usage
    result["live_price_estimate"] = {
        "usd": str(usd),
        "krw_at_2000_buffer": str(usd * 2000),
        "note": "Public short-context standard token prices; reasoning included in output. Not an invoice.",
        "pricing_source": "https://developers.openai.com/api/docs/models/gpt-6-astra",
    }
    result["checks"].update(
        {
            "two_real_azure_calls_verified": True,
            "gpt_reported_both_injected_categories": {"kg", "calculation"}
            <= {c["stage"] for c in before["concerns"]},
            "gpt_recheck_has_no_focused_concerns": not after["concerns"],
            "no_legal_clearance_inferred": not result["rounds"][-1]["dispatch_approved"],
        }
    )
    # Original checks describe the code-only replay, not the subsequent live calls.
    result["checks"].pop("no_paid_model_calls", None)
    result["all_passed"] = all(result["checks"].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("code-trace", "before", "after", "output", "visual"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    result = combine(read(args.code_trace), read(args.before), read(args.after))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    template = Path(__file__).resolve().parents[1] / "examples" / "critic-revision.html"
    fragment = template.read_text(encoding="utf-8").replace(
        "__CRITIC_TRACE__", json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    )
    args.visual.parent.mkdir(parents=True, exist_ok=True)
    args.visual.write_text(fragment, encoding="utf-8")
    print(
        json.dumps(
            {
                "all_passed": result["all_passed"],
                "live_calls": 2,
                "before_concerns": len(result["azure_reviews"]["before"]["concerns"]),
                "after_concerns": len(result["azure_reviews"]["after"]["concerns"]),
                "usage": result["live_usage"],
                "price_estimate": result["live_price_estimate"],
            }
        )
    )
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
