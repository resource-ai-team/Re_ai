"""Opt-in synthetic site-review with a local model; no external inference/map calls."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from resource_ai.core.config import get_settings
from resource_ai.main import app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch

    torch.set_num_threads(4)
    settings = get_settings()
    settings.reranker_provider = "onnx-minilm"
    settings.hybrid_candidate_minimum = 100
    settings.retrieval_article_diversity = False
    settings.road_routing_enabled = False
    settings.legal_graph_model_enabled = False
    settings.legal_graph_allow_external = False
    request = {
        "origin": {
            "organisation_id": "synthetic-org",
            "site_id": "synthetic-site",
            "label": "가상 연구 공장",
            "pickup_address": "가상 수거지",
            "latitude": 37.24,
            "longitude": 127.19,
        },
        "lot": {"material": "PP", "input_form": "scrap", "quantity_kg": 3000, "page_size": 3},
        "max_legal_queries": 1,
        "max_technical_queries": 1,
        "include_lci": False,
    }
    started = perf_counter()
    response = TestClient(app, raise_server_exceptions=False).post(
        "/analysis/site-review", json=request
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input": "fully synthetic",
        "execution": "FastAPI TestClient with real local KG/PostgreSQL/model",
        "external_inference_and_map_calls": 0,
        "status_code": response.status_code,
        "duration_seconds": round(perf_counter() - started, 3),
        "model_scope": "experimental wiring check, rejected as default by retrieval validation",
    }
    if response.status_code == 200:
        data = response.json()
        research = data["review"]["research"]
        hits = [h for s in research["legal_searches"] for h in s["results"]]
        report.update(
            {
                "route": data["route"],
                "candidate_count": len(research["candidates"]),
                "final_recommendations": data["final_recommendations"],
                "dispatch_approved": data["dispatch_approved"],
                "neural_hit_count": sum(h.get("reranker_model") is not None for h in hits),
                "legal_contexts": [
                    {
                        "status": h["legal_context"]["status"],
                        "primary_chunks": len(h["legal_context"]["primary"]),
                        "unresolved_count": len(h["legal_context"].get("unresolved", [])),
                    }
                    for h in hits
                ],
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if response.status_code == 200 and report.get("neural_hit_count", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
