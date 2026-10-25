"""Real local pipeline + simulated Azure transport. No GPT quality/paid-call claim."""

import argparse
import json
import os
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from resource_ai.agents.semantic_critic import SemanticCriticService, get_semantic_critic_service
from resource_ai.core.config import Settings
from resource_ai.evidence.critic_models import SemanticCriticOptions
from resource_ai.integrations.azure_model import AzureResponsesModel
from resource_ai.main import app


def valid_output(payload):
    concerns = []
    for pid, groups in payload["allowed_targets"].items():
        record = next(r for r in payload["evidence"] if r["candidate_id"] == pid)
        concerns.append(
            {
                "stage": "validation",
                "kind": "missing_evidence",
                "candidate_id": pid,
                "target_type": "stage",
                "target_id": groups["stage"][0],
                "message": "가상 검증: 이 경로의 실제 인수 조건은 확인되지 않았습니다.",
                "next_action": "현행 허가와 이번 로트 인수 조건의 서면 확인이 필요합니다.",
                "citations": [{"ref_id": record["ref_id"], "quote": record["text"][:120]}],
            }
        )
    return {"concerns": concerns[: payload["max_findings"]]}


def build_service(mode, capture):
    config = Settings(
        _env_file=None,
        semantic_critic_enabled=True,
        semantic_critic_allow_external=True,
        semantic_critic_service_token="synthetic-contract-test-token",
        semantic_critic_max_input_chars=120000,
    )
    if mode == "disabled":
        config.semantic_critic_enabled = False
    if mode == "external_disabled":
        config.semantic_critic_allow_external = False
    if mode == "budget":
        config.semantic_critic_max_input_chars = 1000

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["input"])
        capture.append(payload)
        output = valid_output(payload)
        if mode == "provider_429":
            return httpx.Response(429, text="synthetic provider failure")
        if mode == "unknown_evidence":
            output["concerns"][0]["citations"][0]["ref_id"] = "E9999"
        if mode == "bad_quote":
            output["concerns"][0]["citations"][0]["quote"] = (
                "입력에 존재하지 않는 가상 원문 문장입니다."
            )
        if mode == "unknown_target":
            output["concerns"][0]["target_id"] = "missing-target"
        if mode == "cross_candidate" and len(output["concerns"]) > 1:
            output["concerns"][0]["citations"] = deepcopy(output["concerns"][1]["citations"])
        if mode == "illegal_verdict":
            output = {"concerns": [], "route": "PASS"}
        if mode == "empty":
            output = {"concerns": []}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "gpt-6-astra",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": json.dumps(output, ensure_ascii=False)}
                        ],
                    }
                ],
            },
        )

    return SemanticCriticService(
        config=config,
        model_factory=lambda: AzureResponsesModel(
            "synthetic-key",
            endpoint="https://synthetic.openai.azure.com",
            transport=httpx.MockTransport(handler),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch

    torch.set_num_threads(4)
    capture = []
    service = build_service("valid", capture)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_semantic_critic_service] = lambda: service
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
        "include_lci": True,
        "semantic_critic": {"max_findings": 4},
    }
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/analysis/site-review",
            json=request,
            headers={"X-Critic-Service-Token": "synthetic-contract-test-token"},
        )
    finally:
        app.dependency_overrides = previous
    if response.status_code != 200:
        raise SystemExit("Synthetic local pipeline did not return HTTP 200")
    from resource_ai.api.schemas.site_review import SiteReviewResponse

    data = SiteReviewResponse.model_validate(response.json())
    critic = data.review.semantic_critic
    checks = [
        {"name": "full pipeline HTTP", "passed": response.status_code == 200},
        {
            "name": "one simulated call after LCI",
            "passed": len(capture) == 1 and "lci" in capture[0]["coverage"]["additional_context"],
        },
        {
            "name": "accepted scoped concerns",
            "passed": critic.status == "completed" and bool(critic.findings),
        },
        {
            "name": "no recommendation or PASS",
            "passed": data.route in {"REVIEW", "REVISE"}
            and not data.final_recommendations
            and not data.dispatch_approved,
        },
    ]
    packet = capture[0] if capture else None
    if packet:
        encoded = json.dumps(packet, ensure_ascii=False)
        checks.append(
            {
                "name": "origin identifiers excluded",
                "passed": all(
                    value not in encoded
                    for value in ["synthetic-org", "synthetic-site", "가상 수거지"]
                ),
            }
        )
    rows = []
    expected = {
        "valid": "completed",
        "unknown_evidence": "partial",
        "bad_quote": "partial",
        "unknown_target": "partial",
        "cross_candidate": "partial",
        "illegal_verdict": "invalid_output",
        "provider_429": "unavailable",
        "empty": "completed",
        "budget": "context_budget_exceeded",
        "disabled": "disabled",
        "external_disabled": "external_transfer_disabled",
    }
    issues = [i.model_dump(mode="json") for i in data.review.critic if i.origin == "code"]
    for mode, status in expected.items():
        observed = []
        result = build_service(mode, observed).run(
            data.review.research,
            data.review.numeric_review,
            None,
            issues,
            SemanticCriticOptions(max_findings=4),
            extra={"site": data.site, "lci": data.lci},
        )
        passed = (
            result.status == status
            and len(observed) <= 1
            and not result.semantic_correctness_verified
        )
        rows.append(
            {
                "case": mode,
                "status": result.status,
                "simulated_calls": len(observed),
                "accepted": len(result.findings),
                "rejected": len(result.rejected_findings),
                "passed": passed,
            }
        )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "real local KG/PostgreSQL/LCI, synthetic lot, simulated Azure; contract tests only",
        "live_gpt_calls": 0,
        "semantic_reasoning_quality_evaluated": False,
        "http_status": response.status_code,
        "candidate_count": len(data.review.candidate_reviews),
        "packet_characters": len(json.dumps(packet, ensure_ascii=False)) if packet else 0,
        "source_kinds": dict(Counter(r["kind"] for r in packet["evidence"])) if packet else {},
        "coverage": critic.coverage,
        "pipeline_checks": checks,
        "adversarial_cases": rows,
        "all_passed": all(c["passed"] for c in checks) and all(r["passed"] for r in rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    print(
        json.dumps(
            {
                "pipeline_checks": len(checks),
                "adversarial_cases": len(rows),
                "packet_characters": summary["packet_characters"],
                "all_passed": summary["all_passed"],
            }
        )
    )
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
