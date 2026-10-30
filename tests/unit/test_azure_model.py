import json

import httpx
import pytest

from resource_ai.core.config import Settings
from resource_ai.integrations.azure_model import AzureResponsesModel, responses_url, strict_schema
from resource_ai.legal_graph.llm import OUTPUTS, configured_model
from resource_ai.legal_graph.models import LegalGraphError

ENDPOINT = "https://synthetic-resource.openai.azure.com"
KEY = "synthetic-key-not-real"


def result(**changes):
    return {
        "status": "completed",
        "model": "gpt-6-astra-2026-09-03",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": '{"summary":"합성 근거 요약"}'}],
            }
        ],
        **changes,
    }


def model(handler):
    return AzureResponsesModel(
        KEY,
        endpoint=ENDPOINT,
        deployment="astra-deployment",
        transport=httpx.MockTransport(handler),
    )


def test_responses_contract_is_astra_high_stateless_and_structured():
    def handler(request):
        assert str(request.url) == ENDPOINT + "/openai/v1/responses"
        assert request.headers["api-key"] == KEY
        body = json.loads(request.content)
        assert body["model"] == "astra-deployment"
        assert body["reasoning"] == {"effort": "high"}
        assert body["store"] is False
        assert body["max_output_tokens"] == 16384
        assert not {"tools", "temperature", "max_tokens", "previous_response_id"} & body.keys()
        assert body["text"]["format"]["strict"] is True
        assert json.loads(body["input"]) == {"evidence": "합성 자료"}
        return httpx.Response(200, json=result())

    assert model(handler).complete("summarize_community", {"evidence": "합성 자료"}) == {
        "summary": "합성 근거 요약"
    }


def test_incomplete_response_keeps_billable_usage_without_saving_provider_content():
    provider = model(
        lambda request: httpx.Response(
            200,
            json=result(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                usage={
                    "input_tokens": 9,
                    "output_tokens": 1024,
                    "output_tokens_details": {"reasoning_tokens": 1000},
                },
            ),
        )
    )
    with pytest.raises(LegalGraphError):
        provider.complete("summarize_community", {})
    assert provider.last_call["usage"] == {
        "input_tokens": 9,
        "output_tokens": 1024,
        "reasoning_tokens": 1000,
    }
    assert provider.last_call["incomplete_reason"] == "max_output_tokens"
    assert "summary" not in json.dumps(provider.last_call)


def test_rate_limit_failure_keeps_only_allowlisted_numeric_headers():
    provider = model(
        lambda request: httpx.Response(
            429,
            headers={
                "x-ratelimit-limit-tokens": "1000",
                "retry-after": "60",
                "x-ratelimit-remaining-tokens": "bad " + KEY,
                "api-key": KEY,
            },
            text="private provider body " + KEY,
        )
    )
    with pytest.raises(LegalGraphError):
        provider.complete("summarize_community", {})
    assert provider.last_call["rate_limits"] == {
        "x-ratelimit-limit-tokens": 1000,
        "retry-after": 60,
    }
    assert KEY not in json.dumps(provider.last_call)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://synthetic-resource.openai.azure.com",
        "https://evil.test",
        "https://synthetic-resource.openai.azure.com.evil.test",
        "https://user:pass@synthetic-resource.openai.azure.com",
        ENDPOINT + "/unexpected",
        ENDPOINT + "?key=secret",
        ENDPOINT + "#secret",
        ENDPOINT + ":8443",
        "https://127.0.0.1",
    ],
)
def test_endpoint_rejects_exfiltration_and_invalid_settings(endpoint):
    with pytest.raises(LegalGraphError, match="エンドポイント|엔드포인트"):
        responses_url(endpoint)


def test_existing_azure_url_forms_and_strict_nested_objects():
    assert responses_url(ENDPOINT + "/openai/v1/") == ENDPOINT + "/openai/v1/responses"
    assert responses_url("https://synthetic.services.ai.azure.com/openai/v1").endswith("/responses")
    schema = strict_schema(OUTPUTS["adjudicate"].model_json_schema())
    finding = schema["$defs"]["Finding"]
    assert "case_ids" in finding["required"]
    assert finding["additionalProperties"] is False
    assert "default" not in finding["properties"]["case_ids"]


def test_azure_alias_requires_operator_checked_model_binding():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=result(model="astra-alias")))
    unchecked = AzureResponsesModel(
        KEY, endpoint=ENDPOINT, deployment="astra-alias", transport=transport
    )
    with pytest.raises(LegalGraphError) as error:
        unchecked.complete("summarize_community", {})
    assert error.value.code == "model-mismatch"
    checked = AzureResponsesModel(
        KEY,
        endpoint=ENDPOINT,
        deployment="astra-alias",
        deployment_model="gpt-6-astra",
        transport=transport,
    )
    assert checked.complete("summarize_community", {})["summary"]
    assert checked.last_call["model_identity_basis"] == "operator_checked_azure_deployment"
    assert "summary" not in checked.last_call and KEY not in str(checked.last_call)


def test_checked_alias_never_accepts_different_model_response():
    provider = AzureResponsesModel(
        KEY,
        endpoint=ENDPOINT,
        deployment="astra-alias",
        deployment_model="gpt-6-astra",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=result(model="gpt-5-mini"))
        ),
    )
    with pytest.raises(LegalGraphError) as error:
        provider.complete("summarize_community", {})
    assert error.value.code == "model-mismatch"


@pytest.mark.parametrize(
    "response",
    [
        result(status="incomplete"),
        result(model="gpt-5-mini"),
        result(output=[]),
        result(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "refused"}],
                }
            ]
        ),
        result(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": '{"wrong":"field"}'}],
                }
            ]
        ),
        {"status": "completed"},
        [],
    ],
)
def test_incomplete_wrong_model_refusal_or_bad_output_never_becomes_decision(response):
    with pytest.raises(LegalGraphError):
        model(lambda r: httpx.Response(200, json=response)).complete("summarize_community", {})


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_provider_failures_are_sanitized_no_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="secret company content " + KEY, headers={"location": "https://evil.test"}
        )

    with pytest.raises(LegalGraphError) as error:
        model(handler).complete("summarize_community", {})
    assert len(calls) == 1
    assert KEY not in str(error.value) and "company content" not in str(error.value)


def test_disabled_and_external_gates_and_no_solar_fallback(monkeypatch):
    import resource_ai.core.config as config_module

    config = Settings(_env_file=None)
    monkeypatch.setattr(config_module, "get_settings", lambda: config)
    with pytest.raises(LegalGraphError) as error:
        configured_model()
    assert error.value.code == "model-not-configured"
    config.legal_graph_model_enabled = True
    with pytest.raises(LegalGraphError) as error:
        configured_model()
    assert error.value.code == "model-transfer-disabled"
    config.legal_graph_allow_external = True
    with pytest.raises(LegalGraphError) as error:
        configured_model()
    assert error.value.code == "model-not-configured"
    assert "azure_openai_api_key" not in repr(config)


def test_budget_rejected_before_network():
    provider = model(lambda r: pytest.fail("must not call"))
    with pytest.raises(LegalGraphError) as error:
        provider.complete("summarize_community", {"text": "x" * 150001})
    assert error.value.code == "context-budget"
