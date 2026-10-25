"""One explicit synthetic-only Azure call. Never prints keys or source documents."""

import argparse
import json

from resource_ai.core.config import get_settings
from resource_ai.integrations.azure_model import AzureResponsesModel
from resource_ai.legal_graph.models import LegalGraphError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-synthetic", action="store_true", required=True)
    parser.parse_args()
    config = get_settings()
    model = None
    try:
        model = AzureResponsesModel(
            config.azure_openai_api_key.get_secret_value(),
            endpoint=config.azure_openai_endpoint,
            deployment=config.azure_openai_deployment,
            deployment_model=config.azure_openai_deployment_model,
            reasoning_effort=config.azure_openai_reasoning_effort,
            max_output_tokens=2048,
            timeout=180,
        )
        output = model.complete(
            "summarize_community",
            {
                "synthetic": True,
                "member_features": [
                    {
                        "actor": "fictional waste producer",
                        "conduct": "consider recycling",
                        "object": "fictional PP scrap",
                        "context": "permit status unknown",
                    }
                ],
                "issues": ["Do not infer a permit or final recommendation."],
            },
        )
    except LegalGraphError as error:
        print(
            json.dumps(
                {"status": "failed", "code": error.code, "call": model.last_call if model else {}},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "completed",
                "verified_model": model.name,
                "reasoning_effort": model.reasoning_effort,
                "synthetic_only": True,
                "schema_valid": bool(output["summary"]),
                "calls": 1,
                "call": model.last_call,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
