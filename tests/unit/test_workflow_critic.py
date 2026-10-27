"""Model transport is replaced; verifies permission filtering, citations and map binding."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from resource_ai.workflow import critic
from resource_ai.workflow.service import recompute, repair_units
from tests.unit.test_evidence_workflow import prepared


@pytest.fixture(scope="module")
def state():
    return prepared()[1]


class Model:
    def __init__(self, reply):
        self.reply = reply
        self.payload = None
        self.last_call = {"usage": {}}

    def complete(self, task, payload):
        assert task == "semantic_critic"
        self.payload = payload
        return self.reply


def service(model):
    return SimpleNamespace(
        unavailable_status=lambda: None,
        config=SimpleNamespace(semantic_critic_max_input_chars=100000),
        _model=lambda: model,
    )


def test_no_external_source_permission_means_zero_calls(state):
    model = Model({"concerns": []})
    result = critic.WorkflowCritic(service(model))(deepcopy(state))
    assert result["model_calls"] == 0 and model.payload is None


def test_permitted_exact_citation_becomes_question_not_confirmed_failure(state, monkeypatch):
    state = deepcopy(state)
    state["inputs"]["prompt"] = "PRIVATE_ORIGIN_PROMPT_MUST_NOT_LEAVE"
    state["evidence"]["synthetic-evidence"]["external_model_allowed"] = True
    pid = next(iter(state["drafts"]))
    quote = state["evidence"]["synthetic-evidence"]["content"]
    model = Model(
        {
            "concerns": [
                {
                    "stage": "calculation",
                    "kind": "potential_error",
                    "candidate_id": pid,
                    "target_type": "candidate",
                    "target_id": pid,
                    "message": "가상 자료의 단위를 대조해야 합니다.",
                    "next_action": "원문 단가 단위를 확인해 주세요.",
                    "citations": [{"ref_id": "E0001", "quote": quote}],
                }
            ]
        }
    )
    monkeypatch.setattr(critic, "_last_started", 0)
    result = critic.WorkflowCritic(service(model), clock=lambda: 1000)(state)
    assert result["model_calls"] == 1 and len(result["findings"]) == 1
    assert "PRIVATE_ORIGIN_PROMPT_MUST_NOT_LEAVE" not in str(model.payload)
    state["critic_runs"] = [result]
    recompute(state)
    marker = next(m for m in state["evaluation"]["map"]["markers"] if m.get("origin") == "model")
    assert marker["symbol"] == "?" and marker["status"] == "unknown"
    assert not state["evaluation"]["dispatch_approved"]


def test_critic_rate_limit_does_not_send_a_second_request(state, monkeypatch):
    state = deepcopy(state)
    state["evidence"]["synthetic-evidence"]["external_model_allowed"] = True
    model = Model({"concerns": []})
    monkeypatch.setattr(critic, "_last_started", 1000)
    result = critic.WorkflowCritic(service(model), clock=lambda: 1001)(state)
    assert result["model_calls"] == 0 and model.payload is None


@pytest.mark.parametrize("raw_text", ["1000000 KRW/t", "100000 kg; the quote is 20 KRW/t"])
def test_unit_repair_does_not_combine_unrelated_numbers_and_units(state, raw_text):
    state = deepcopy(state)
    state["evidence"]["synthetic-evidence"]["content"] = raw_text
    line = next(l for d in state["drafts"].values() for l in d["lines"] if l["metric"] == "cost")
    line["factor"].update(low="100000", high="100000", unit="KRW/t")
    assert repair_units(state) == []
