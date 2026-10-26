"""Offline scope/citation/budget checks; importing this file never calls Azure."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from resource_ai.knowledge_graph.ontology import digest
from scripts.build_azure_critic_replay import combine
from scripts.run_focused_azure_critic import (
    MAX_INPUT_UPPER_TOKENS,
    focused_packet,
    input_upper_tokens,
    main,
)


def read(name):
    return json.loads(Path("docs/evaluation", name).read_text(encoding="utf-8"))


def fixtures():
    return (
        read("azure-critic-code-repair-2026-10-27.json"),
        read("azure-critic-before-2026-10-27.json"),
        read("azure-critic-after-2026-10-27.json"),
    )


def test_focused_packet_changes_draft_but_keeps_the_same_kg_quote_and_scope():
    trace, _, _ = fixtures()
    trace["private_document"] = "MUST-NOT-TRANSFER"
    trace["rounds"][0]["focus"]["facility_address"] = "MUST-NOT-TRANSFER"
    before, records, targets = focused_packet(trace, "before")
    after, _, _ = focused_packet(trace, "after")
    assert before["evidence"][:2] == after["evidence"][:2]
    assert before["evidence"][2] != after["evidence"][2]
    assert "MUST-NOT-TRANSFER" not in json.dumps(before)
    assert before["coverage"]["candidate_count"] == 1 and set(targets) == {"A"}
    assert input_upper_tokens(before) <= MAX_INPUT_UPPER_TOKENS
    assert "evidence_id" not in before["evidence"][0] and "evidence_id" in records["E0001"]
    with pytest.raises(ValueError, match="Unknown"):
        focused_packet(trace, "unknown")


def test_live_receipts_are_revalidated_before_being_labelled_live():
    trace, before, after = fixtures()
    result = combine(trace, before, after)
    assert result["all_passed"] and result["provenance"]["gpt_calls"] == 2
    assert result["live_usage"]["output_tokens"] == 955
    assert result["live_price_estimate"]["usd"] == "0.06458"  # Reasoning is not billed twice.
    assert not result["rounds"][-1]["dispatch_approved"]


@pytest.mark.parametrize("corrupt", ["model", "quote", "input_hash", "effort"])
def test_forged_live_receipts_do_not_become_a_successful_replay(corrupt):
    trace, before, after = fixtures()
    if corrupt == "model":
        before["call"]["verified_model"] = "other-model"
    elif corrupt == "quote":
        before["concerns"][0]["citations"][0]["quote"] = "not found in the supplied source text"
    elif corrupt == "input_hash":
        before["input_sha256"] = "0" * 64
    else:
        after["call"]["reasoning_effort"] = "low"
    with pytest.raises(ValueError):
        combine(trace, before, after)


def test_paid_cli_has_no_automatic_retry_and_prevents_a_third_call(monkeypatch, tmp_path):
    import scripts.run_focused_azure_critic as module

    trace, _, _ = fixtures()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(trace), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            self.last_call = {}
            assert kwargs["reasoning_effort"] == "high" and kwargs["max_output_tokens"] == 1024

        def complete(self, task, payload):
            calls.append(task)
            self.last_call = {
                "verified_model": "gpt-6-astra",
                "usage": {"input_tokens": 5, "output_tokens": 5},
            }
            return {"concerns": []}

    monkeypatch.setattr(module, "AzureResponsesModel", FakeModel)

    def run(phase, before=None):
        args = [
            "probe",
            "--live-public-synthetic",
            "--phase",
            phase,
            "--source",
            str(source),
            "--output",
            str(tmp_path / (phase + ".json")),
            "--ledger",
            str(ledger),
        ]
        if before:
            args.extend(["--before-receipt", str(before)])
        monkeypatch.setattr(sys, "argv", args)
        return main()

    assert run("before") == 0
    assert run("after", tmp_path / "before.json") == 0
    with pytest.raises(ValueError, match="two total"):
        run("before")
    assert calls == ["focused_critic", "focused_critic"]
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(saved["attempts"]) == 2
    assert saved["approved_test_cap_krw"] == 1000


def test_changed_kg_source_has_a_different_packet_hash():
    trace, _, _ = fixtures()
    original, _, _ = focused_packet(trace, "after")
    changed = deepcopy(trace)
    changed["rounds"][-1]["focus"]["stages"][0]["label"] = "A different process"
    packet, _, _ = focused_packet(changed, "after")
    assert digest(original) != digest(packet)
