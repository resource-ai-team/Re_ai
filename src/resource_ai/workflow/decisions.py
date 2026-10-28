"""Evidence-bound numeric intake conditions and deterministic decision support.

No inferred facility limits, legal approvals, probability or autonomous model edits.
"""

import re
from copy import deepcopy
from decimal import Decimal

from resource_ai.knowledge_graph.ontology import digest, identity
from resource_ai.workflow.calculation import issues_for_source
from resource_ai.workflow.documents import reviewable_fields
from resource_ai.workflow.models import ConditionsUpdate, WhatIfRequest
from resource_ai.workflow.store import WorkflowError

FIELDS = {
    "moisture_pct": ("함수율", "%", "함수율|수분"),
    "contamination_pct": ("오염도", "%", "오염도|오염함량"),
    "quantity_kg": ("물량", "kg", "물량|중량|수량"),
}
OPS = {
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "eq": lambda a, b: a == b,
}
OP_TEXT = {"le": "≤", "lt": "<", "ge": "≥", "gt": ">", "eq": "="}


def candidates(state):
    return (state.get("engine") or {}).get("review", {}).get("research", {}).get("candidates", [])


def lot_fingerprint(state):
    r = state["inputs"].get("engine_request") or {}
    return digest({"origin": r.get("origin"), "lot": r.get("lot")})


def rule_scope(state, condition):
    lot = (state["inputs"].get("engine_request") or {}).get("lot", {})
    return digest([condition["condition_id"], lot.get("material"), lot.get("input_form")])


def condition_catalog(state):
    rows = []
    trace = (state.get("engine") or {}).get("site", {}).get("graph_trace", {})
    for candidate in candidates(state):
        plan = candidate["plan"]
        selected = {
            f["stage_id"]: f for f in state["drafts"].get(plan["plan_id"], {}).get("facilities", [])
        }
        for index, stage in enumerate(plan["research_packet"]["stages"]):
            facility = selected.get(stage["stage_id"]) or stage.get("facility") or {}
            fid = facility.get("facility_id") or facility.get("key")
            # Numeric fields already exist as explicit unresolved checks in our KG intake model.
            checks = next(
                (
                    s["assessment"]["checks"]
                    for s in candidate.get("facility_assessment", {}).get("stages", [])
                    if s["stage_id"] == stage["stage_id"]
                ),
                [],
            )
            for check in checks:
                field = check["code"]
                if field not in FIELDS:
                    continue
                label, unit, _ = FIELDS[field]
                cid = identity(
                    "intake-condition", [trace, plan["plan_id"], stage["stage_id"], fid, field]
                )
                rows.append(
                    {
                        "condition_id": cid,
                        "candidate_id": plan["plan_id"],
                        "target_id": stage["stage_id"],
                        "facility_id": fid,
                        "facility_label": facility.get("label"),
                        "stage_label": stage["transition"]["label"],
                        "field": field,
                        "label": label,
                        "unit": unit,
                        "stage_index": index,
                        "kg_source_ids": check.get("source_ids", []),
                        "kg_scope": check.get("scope"),
                        "requirement": None,
                        "observation": None,
                        "status": "unknown",
                        "missing": [],
                        "scope": "submitted_evidence_only_not_legal_or_dispatch_approval",
                    }
                )
    return rows


def parse_limits(content, field):
    """A quoted explicit comparator is required. Lists/examples never become limits."""
    _, unit, labels = FIELDS[field]
    number = r"\d+(?:,\d{3})*(?:\.\d+)?"
    pattern = re.compile(
        rf"(?:{labels})\s*[:：]?\s*(?:(?P<op><=|>=|≤|≥|<|>|=)\s*)?(?P<n>{number})\s*(?P<unit>%|kg|톤|t)(?=$|[\s,.;:]|이하|미만|이상|초과|동일)\s*(?P<ko>이하|미만|이상|초과|동일)?",
        re.IGNORECASE,
    )
    values = []
    operators = {
        "<=": "le",
        "≤": "le",
        ">=": "ge",
        "≥": "ge",
        "<": "lt",
        ">": "gt",
        "=": "eq",
        "이하": "le",
        "미만": "lt",
        "이상": "ge",
        "초과": "gt",
        "동일": "eq",
    }
    for m in pattern.finditer(content):
        if len(m["n"]) > 24:
            continue
        before, after = content[max(0, m.start() - 10) : m.start()], content[m.end() : m.end() + 25]
        if re.search(r"(?:예상|추정|약)\s*$", before) or re.match(
            r"\s*(?:[~～±–—-]|또는|혹은|to\b|or\b)", after, re.IGNORECASE
        ):
            continue
        op = operators.get(m["op"] or m["ko"])
        if not op or (m["op"] and m["ko"] and operators[m["op"]] != operators[m["ko"]]):
            continue
        actual_unit = m["unit"].lower()
        if (unit == "%") != (actual_unit == "%"):
            continue
        value = Decimal(m["n"].replace(",", "")) * (1000 if actual_unit in ("t", "톤") else 1)
        if value > (100 if unit == "%" else Decimal("1e12")):
            continue
        values.append((op, value, m[0]))
    return values


def valid_source(state, eid):
    return not issues_for_source(eid, state["evidence"], state["inputs"])


def source_fingerprint(source):
    return digest({k: v for k, v in source.items() if k != "external_model_allowed"})


def apply_reviews(state, payload, actor, at):
    update = ConditionsUpdate.model_validate({**payload, "expected_revision": state["revision"]})
    catalog = {r["condition_id"]: r for r in condition_catalog(state)}
    reviews = state.setdefault("condition_reviews", {"rules": {}, "readings": {}})
    for group, items, removed in (
        ("rules", update.rules, update.remove_rules),
        ("readings", update.readings, update.remove_readings),
    ):
        ids = [r.condition_id for r in items]
        if len(ids) != len(set(ids)) or set(ids) & set(removed):
            raise WorkflowError("duplicate-condition-operation", 422)
        if any(cid not in catalog for cid in [*ids, *removed]):
            raise WorkflowError("condition-not-in-current-graph", 422)
        for cid in removed:
            reviews[group].pop(cid, None)
        for item in items:
            c = catalog[item.condition_id]
            if item.unit != c["unit"]:
                raise WorkflowError("condition-unit-mismatch", 422)
            value = item.limit if group == "rules" else item.value
            if c["unit"] == "%" and value > 100:
                raise WorkflowError("condition-percent-out-of-range", 422)
            if not valid_source(state, item.evidence_id):
                raise WorkflowError("condition-evidence-unconfirmed-or-stale", 422)
            source = state["evidence"][item.evidence_id]
            if group == "rules":
                if (
                    not item.applicability_confirmed
                    or not c["facility_id"]
                    or item.facility_id != c["facility_id"]
                ):
                    raise WorkflowError("condition-facility-applicability-required", 422)
                limits = parse_limits(source["content"], c["field"])
                if len({(op, n) for op, n, _ in limits}) > 1:
                    raise WorkflowError("ambiguous-facility-limit-excerpt", 422)
                quotes = [q for op, n, q in limits if op == item.operator and n == item.limit]
            else:
                if not item.same_lot_and_stage_confirmed:
                    raise WorkflowError("condition-lot-and-stage-confirmation-required", 422)
                extraction = {"pages": [{"page": 1, "text": source["content"]}]}
                observations = [
                    r
                    for r in reviewable_fields(item.evidence_id, extraction)
                    if r["can_apply"] and r["target_field"] == c["field"]
                ]
                if len({Decimal(r["normalized_value"]) for r in observations}) > 1:
                    raise WorkflowError("ambiguous-stage-measurement-excerpt", 422)
                quotes = [
                    r["quote"]
                    for r in observations
                    if r["can_apply"]
                    and r["target_field"] == c["field"]
                    and Decimal(r["normalized_value"]) == value
                ]
            if not quotes:
                raise WorkflowError("condition-literal-not-in-source", 422)
            reviews[group][item.condition_id] = {
                **item.model_dump(mode="json"),
                "source_sha256": source_fingerprint(source),
                "quote": quotes[0],
                "confirmed_by": actor,
                "confirmed_at": at,
                "lot_fingerprint": lot_fingerprint(state) if group == "readings" else None,
                "input_scope": rule_scope(state, c) if group == "rules" else None,
            }


def record_usable(record, state):
    return (
        record
        and valid_source(state, record["evidence_id"])
        and record["source_sha256"]
        == source_fingerprint(state["evidence"].get(record["evidence_id"], {}))
    )


def build_conditions(state, assumptions=None):
    reviews = state.get("condition_reviews", {})
    rows = condition_catalog(state)
    for c in rows:
        cid = c["condition_id"]
        rule = reviews.get("rules", {}).get(cid)
        reading = reviews.get("readings", {}).get(cid)
        if record_usable(rule, state) and rule.get("input_scope") == rule_scope(state, c):
            c["requirement"] = deepcopy(rule)
        else:
            c["missing"].append("facility_limit_evidence")
        if record_usable(reading, state) and reading["lot_fingerprint"] == lot_fingerprint(state):
            c["observation"] = deepcopy(reading)
        elif c["stage_index"] == 0:
            fact = state.get("field_provenance", {}).get(c["field"])
            current = (state["inputs"].get("engine_request") or {}).get("lot", {}).get(c["field"])
            if (
                fact
                and current is not None
                and Decimal(str(current)) == Decimal(fact["normalized_value"])
                and valid_source(state, fact["evidence_id"])
            ):
                c["observation"] = {
                    "value": str(current),
                    "unit": c["unit"],
                    "evidence_id": fact["evidence_id"],
                    "quote": fact["quote"],
                    "basis": "confirmed_origin_lot_field_first_stage_only",
                }
        if assumptions and cid in assumptions:
            c["observation"] = {
                **assumptions[cid],
                "basis": "hypothetical_not_observed",
                "evidence_id": None,
            }
            c["hypothetical"] = True
        if c["observation"] is None:
            c["missing"].append("stage_measurement_evidence")
        if c["requirement"] and c["observation"]:
            r, o = c["requirement"], c["observation"]
            c["status"] = (
                "met" if OPS[r["operator"]](Decimal(o["value"]), Decimal(r["limit"])) else "unmet"
            )
            c["explanation"] = (
                f"{c['label']} {o['value']} {c['unit']} / 제출 기준 {OP_TEXT[r['operator']]} {r['limit']} {c['unit']}"
            )
        else:
            c["explanation"] = " · ".join(
                "시설 수탁 기준 필요"
                if k == "facility_limit_evidence"
                else "해당 단계의 로트 측정값 필요"
                for k in c["missing"]
            )
    return rows


def decision_support(state, conditions, results=None):
    gates = []
    for candidate in candidates(state):
        pid = candidate["plan"]["plan_id"]
        own = [c for c in conditions if c["candidate_id"] == pid]
        blocked = candidate["status"] == "unmet" or any(c["status"] == "unmet" for c in own)
        # Checking numeric intake limits never clears permit, acceptance, capacity or legal gates.
        gates.append(
            {
                "candidate_id": pid,
                "status": "blocked" if blocked else "hold",
                "condition_ids": [c["condition_id"] for c in own],
                "unmet_condition_ids": [c["condition_id"] for c in own if c["status"] == "unmet"],
                "unknown_condition_ids": [
                    c["condition_id"] for c in own if c["status"] == "unknown"
                ],
                "remaining_gates": [
                    "current_permit_scope",
                    "legal_applicability",
                    "lot_acceptance",
                    "available_capacity",
                    "handoff_specifications",
                ],
                "dispatch_approved": False,
                "reason": "제출 조건 불일치"
                if blocked
                else "현행 허가·법규 적용·이번 로트 인수 등 별도 확인 필요",
            }
        )
    grouped = {}
    for result in (
        results if results is not None else state.get("evaluation", {}).get("results", [])
    ):
        for issue in result.get("errors", []):
            key = identity("decision-action", [result["candidate_id"], issue])
            grouped[key] = {
                "action_id": key,
                "candidate_id": result["candidate_id"],
                "target_id": issue["target_id"],
                "condition_ids": [],
                "priority": 0,
                "kind": "numeric_conflict",
                "title": "계산·연결 충돌 확인 · " + issue["code"],
                "owner": "자료·계산 담당자",
                "required_document": "해당 계산 항목의 원문·단위·활동량·범위",
                "why": "코드로 확인한 충돌을 해결하기 전에는 전체 비교에 사용할 수 없습니다.",
                "expected_effect": ["numeric_consistency", "comparison_eligibility"],
                "probability": None,
            }
    for gate in gates:
        key = identity("decision-action", [gate["candidate_id"], "external-gates"])
        grouped[key] = {
            "action_id": key,
            "candidate_id": gate["candidate_id"],
            "target_id": gate["candidate_id"],
            "condition_ids": [],
            "priority": 1,
            "kind": "external_gates",
            "title": "법규·허가·이번 로트 인수 조건 확인",
            "owner": "환경 담당자·시설 담당자",
            "required_document": "적용 법규·예외 검토, 현행 허가 범위, 서면 수탁 조건과 여유 용량",
            "why": "수분·오염도 수치가 맞아도 이 확인 전에는 실행 가능한 경로로 확정할 수 없습니다.",
            "expected_effect": ["legal_and_acceptance_review"],
            "probability": None,
        }
    for c in conditions:
        if c["status"] == "met":
            continue
        need = (
            "resolve_mismatch"
            if c["status"] == "unmet"
            else "facility_limit_evidence"
            if "facility_limit_evidence" in c["missing"]
            else "stage_measurement_evidence"
        )
        key = identity(
            "decision-action",
            [c["facility_id"], c["candidate_id"], c["target_id"], c["field"], need],
        )
        grouped[key] = {
            "action_id": key,
            "candidate_id": c["candidate_id"],
            "target_id": c["target_id"],
            "condition_ids": [c["condition_id"]],
            "priority": 0
            if need == "resolve_mismatch"
            else 2
            if need == "facility_limit_evidence"
            else 3,
            "kind": need,
            "title": c["label"] + " · " + c["explanation"],
            "owner": "시설·품질 담당자"
            if need == "resolve_mismatch"
            else "시설 담당자"
            if need == "facility_limit_evidence"
            else "품질·시험 담당자",
            "required_document": "이 시설·공정의 수탁 기준서"
            if need == "facility_limit_evidence"
            else "시설 수탁 기준과 같은 로트·단계의 성적서"
            if need == "resolve_mismatch"
            else "해당 단계 시료의 성적서·계량 근거",
            "why": "이 조건이 맞지 않으면 해당 후보의 비교를 보류합니다. 조건 충족만으로 허가·인수를 확정하지 않습니다.",
            "expected_effect": ["condition_status", "candidate_gate", "comparison_eligibility"],
            "probability": None,
        }
    # Every hard blocker stays available; top three is a navigation aid, never a pass decision.
    actions = sorted(grouped.values(), key=lambda a: (a["priority"], a["action_id"]))
    return {
        "version": "condition-decision-v2",
        "conditions": conditions,
        "candidate_gates": gates,
        "next_actions": actions,
        "first_actions": actions[:3],
        "not_covered": [
            "legal_or_acceptance_approval",
            "automatic_threshold_inference",
            "causal_cost_or_carbon_model_for_moisture",
        ],
        "dispatch_approved": False,
    }


def delta(before, after):
    old = (before.get("evaluation") or {}).get("decision_support", {})
    new = after["evaluation"].get("decision_support", {})
    prev = {c["condition_id"]: c for c in old.get("conditions", [])}
    changes = []
    for c in new.get("conditions", []):
        p = prev.get(c["condition_id"])
        keys = ("status", "requirement", "observation", "missing")
        if p is None or any(p.get(k) != c.get(k) for k in keys):
            changes.append(
                {
                    "condition_id": c["condition_id"],
                    "candidate_id": c["candidate_id"],
                    "target_id": c["target_id"],
                    "label": c["label"],
                    "before_status": p["status"] if p else None,
                    "after_status": c["status"],
                    "explanation": c["explanation"],
                    "evidence_ids": [
                        r["evidence_id"]
                        for r in (c["requirement"], c["observation"])
                        if r and r.get("evidence_id")
                    ],
                }
            )
    previous_metrics = {
        r["candidate_id"]: {k: r.get(k) for k in ("totals", "known_subtotals", "comparable")}
        for r in (before.get("evaluation") or {}).get("results", [])
    }
    metrics = []
    for row in after["evaluation"].get("results", []):
        new_metrics = {k: row.get(k) for k in ("totals", "known_subtotals", "comparable")}
        if previous_metrics.get(row["candidate_id"]) != new_metrics:
            metrics.append(
                {
                    "candidate_id": row["candidate_id"],
                    "before": previous_metrics.get(row["candidate_id"]),
                    "after": new_metrics,
                }
            )
    return {
        "from_revision": before.get("revision"),
        "to_revision": before.get("revision", 0) + 1,
        "condition_changes": changes,
        "removed_condition_ids": sorted(
            set(prev) - {c["condition_id"] for c in new.get("conditions", [])}
        ),
        "metric_changes": metrics,
        "pareto_before": (before.get("evaluation") or {}).get("pareto_for_review", []),
        "pareto_after": after["evaluation"].get("pareto_for_review", []),
        "scope": "condition_and_comparison_change_not_dispatch_approval",
    }


def what_if(state, request):
    state = deepcopy(state)
    if (
        state.get("evaluation", {}).get("decision_support", {}).get("version")
        != "condition-decision-v2"
    ):
        state["evaluation"]["decision_support"] = decision_support(state, build_conditions(state))
    request = WhatIfRequest.model_validate(request)
    if state["revision"] != request.expected_revision:
        raise WorkflowError("revision-conflict", 409)
    if state.get("active_operation"):
        raise WorkflowError("case-operation-in-progress", 409)
    catalog = {r["condition_id"]: r for r in condition_catalog(state)}
    ids = [r.condition_id for r in request.readings]
    if len(ids) != len(set(ids)):
        raise WorkflowError("duplicate-assumed-condition", 422)
    overrides = {}
    for r in request.readings:
        c = catalog.get(r.condition_id)
        if not c:
            raise WorkflowError("condition-not-in-current-graph", 422)
        if c["field"] == "quantity_kg":
            raise WorkflowError("quantity-simulation-needs-mass-and-activity-model", 422)
        if r.unit != c["unit"] or r.value > 100:
            raise WorkflowError("invalid-assumed-unit-or-percent", 422)
        overrides[r.condition_id] = r.model_dump(mode="json")
    preview = deepcopy(state)
    preview["evaluation"]["decision_support"] = decision_support(
        preview, build_conditions(preview, overrides)
    )
    change = delta(state, preview)
    change["to_revision"] = None
    return {
        "scope": "hypothetical_not_saved_or_observed",
        "preview_id": identity("hypothesis", [state["case_id"], state["revision"], overrides]),
        "base_case_id": state["case_id"],
        "base_revision": state["revision"],
        "assumptions": list(overrides.values()),
        "decision_support": preview["evaluation"]["decision_support"],
        "changes": change,
        "cost_and_carbon": None,
        "numeric_scope": "No measured yield/activity model for these assumptions; no numeric ranking computed",
        "dispatch_approved": False,
    }
