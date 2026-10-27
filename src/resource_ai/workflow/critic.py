"""Astra adapter with explicit source transfer permissions and exact quote checks."""

import json
import threading
import time

from resource_ai.agents.semantic_critic import SemanticCriticService
from resource_ai.evidence.critic_models import SemanticCriticOutput
from resource_ai.knowledge_graph.ontology import digest

_slot = threading.Lock()
_last_started = 0.0


class WorkflowCritic:
    def __init__(self, service, *, clock=time.monotonic, slot_store=None):
        self.service, self.clock, self.slot_store = service, clock, slot_store

    def __call__(self, state):
        global _last_started
        unavailable = self.service.unavailable_status()
        if unavailable:
            return {"status": unavailable, "model_calls": 0}
        sources = {
            k: v
            for k, v in state["evidence"].items()
            if v.get("external_model_allowed") and v.get("user_confirmed")
        }
        records = {}
        for i, source in enumerate(sources.values(), 1):
            records[f"E{i:04d}"] = {
                "candidate_id": None,
                "evidence_id": source["evidence_id"],
                "text": source["content"],
            }
        if not records:
            return {"status": "no_explicitly_permitted_evidence", "model_calls": 0}
        targets, candidates = {}, []
        evidence_bindings = {key: set() for key in sources}
        for candidate in (
            (state.get("engine") or {}).get("review", {}).get("research", {}).get("candidates", [])
        ):
            plan = candidate["plan"]
            packet = plan["research_packet"]
            pid = plan["plan_id"]
            targets[pid] = {
                "candidate": {pid},
                "stage": {s["stage_id"] for s in packet["stages"]},
                "handoff": {h["leg_id"] for h in packet["handoffs"]} | {f"{pid}:origin-transport"},
            }
            draft = state["drafts"].get(pid, {})
            permitted_lines = [
                line
                for line in draft.get("lines", [])
                if line["activity"]["evidence_id"] in sources
                and line["factor"]["evidence_id"] in sources
            ]
            for line in permitted_lines:
                for observation in (line["activity"], line["factor"]):
                    evidence_bindings[observation["evidence_id"]].add(pid)
            for selection in [*draft.get("facilities", []), *draft.get("tails", [])]:
                if selection["evidence_id"] in evidence_bindings:
                    evidence_bindings[selection["evidence_id"]].add(pid)
            candidates.append(
                {
                    "candidate_id": pid,
                    "allowed_targets": {k: sorted(v) for k, v in targets[pid].items()},
                    "declared_lines": permitted_lines,
                    "withheld_lines": len(draft.get("lines", [])) - len(permitted_lines),
                }
            )
        payload = {
            "max_findings": 8,
            "candidates": candidates,
            "records": [{"ref_id": key, "text": row["text"]} for key, row in records.items()],
            "scope": "explicitly_permitted_numeric_sources_only; no permit approval; private prompts/origin/documents withheld",
        }
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > self.service.config.semantic_critic_max_input_chars:
            return {"status": "context_budget_exceeded", "model_calls": 0}
        if not _slot.acquire(blocking=False):
            return {"status": "model_busy", "model_calls": 0}
        claimed = False
        try:
            if _last_started and self.clock() - _last_started < 60:
                return {
                    "status": "rate_limit_wait_required",
                    "model_calls": 0,
                    "retry_after_seconds": 60,
                }
            if self.slot_store:
                claimed = self.slot_store.claim_provider("workflow-astra")
                if not claimed:
                    return {
                        "status": "rate_limit_wait_required",
                        "model_calls": 0,
                        "retry_after_seconds": 60,
                    }
            _last_started = self.clock()
            model = self.service._model()
            response = model.complete("semantic_critic", payload)
            output = SemanticCriticOutput.model_validate(response)
            findings, rejected = [], []
            for concern in output.concerns:
                reason = SemanticCriticService._check(concern, records, targets)
                if (
                    not reason
                    and concern.candidate_id is not None
                    and any(
                        evidence_bindings[records[c.ref_id]["evidence_id"]]
                        and concern.candidate_id
                        not in evidence_bindings[records[c.ref_id]["evidence_id"]]
                        for c in concern.citations
                    )
                ):
                    reason = "cross_candidate_evidence"
                if reason:
                    rejected.append(reason)
                else:
                    findings.append(
                        {
                            **concern.model_dump(mode="json"),
                            "evidence_ids": sorted(
                                {records[c.ref_id]["evidence_id"] for c in concern.citations}
                            ),
                        }
                    )
            return {
                "status": "completed" if not rejected else "partial",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
                "model_calls": 1,
                "input_sha256": digest(payload),
                "findings": findings,
                "rejected": rejected,
                "usage": model.last_call.get("usage", {}),
                "semantic_correctness_verified": False,
                "dispatch_approved": False,
            }
        except Exception:  # noqa: BLE001 - no source/provider body in errors.
            return {"status": "model_unavailable", "model_calls": 1}
        finally:
            if claimed:
                self.slot_store.release_provider("workflow-astra")
            _slot.release()
