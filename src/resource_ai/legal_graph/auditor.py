"""Item Check -> separate article Decide -> result-scoped pruning, not DB deletion."""

from resource_ai.legal_graph.llm import checked_output
from resource_ai.legal_graph.models import LegalGraphError


class Auditor:
    def __init__(self, snapshot, model):
        self.snapshot, self.model = snapshot, model

    def audit(self, research, facts, *, max_articles=20):
        nodes, evidence = self.snapshot.node_map(), self.snapshot.evidence_map()
        records, verified, rejected, unknown = [], [], [], []
        for index, aid in enumerate(research["article_ids"]):
            article = nodes[aid]
            allowed = set(research["eligible_evidence_ids"][aid])
            # Article.text can contain paragraphs not yet in force. Send only eligible spans.
            spans = [evidence[eid].model_dump(mode="json") for eid in sorted(allowed)]
            original = "\n\n".join(
                evidence[eid].text for eid in article.evidence_ids if eid in allowed
            )
            items = [
                nodes[e.target]
                for e in self.snapshot.edges
                if e.source == aid and e.relation == "HAS_CHECKLIST"
            ]
            interpretations = []
            for edge in self.snapshot.edges:
                if edge.source != aid or edge.relation != "HAS_INTERPRETATION":
                    continue
                node = nodes[edge.target]
                valid = [
                    eid
                    for eid in node.evidence_ids
                    if evidence[eid].effective_from is not None
                    and evidence[eid].effective_from.isoformat() <= research["as_of"]
                ]
                if valid and node.data.get("version_verified") is True:
                    interpretations.append(
                        {
                            "id": node.id,
                            "text": "\n".join(evidence[eid].text for eid in valid),
                            "evidence_ids": valid,
                        }
                    )
                    allowed.update(valid)
                    spans.extend(evidence[eid].model_dump(mode="json") for eid in valid)
            record = {
                "article_id": aid,
                "label": article.label,
                "status": "unknown",
                "reason": "검증 미완료",
                "evidence_ids": [],
                "decisive_item_ids": [],
                "items": [],
                "error_code": None,
                "checklist_exhaustiveness_verified": article.data.get(
                    "checklist_exhaustiveness_verified"
                )
                is True,
                "interpretation_ids": [i["id"] for i in interpretations],
            }
            base = {
                "article_id": aid,
                "article_label": article.label,
                "article_text": original,
                "evidence": spans,
                "interpretations": interpretations,
                "facts": facts,
                "as_of": research["as_of"],
            }
            try:
                if index >= max_articles:
                    raise LegalGraphError(
                        "article-budget", "조문 검증 한도를 넘어 확인이 필요합니다."
                    )
                if not items:
                    raise LegalGraphError(
                        "missing-checklist", "원문 기반 요건 체크리스트가 없습니다."
                    )
                if article.data.get("required_version_verified") is False:
                    raise LegalGraphError(
                        "unresolved-version", "적용에 필요한 법령 버전이 확인되지 않았습니다."
                    )
                if article.data.get("requires_interpretation") is True and not interpretations:
                    raise LegalGraphError(
                        "missing-interpretation", "필요한 공식 해석 근거가 없습니다."
                    )
                for item in items:
                    eid, quote = item.data.get("evidence_id"), item.data.get("quote")
                    start, end = item.data.get("start"), item.data.get("end")
                    check = {
                        "item_id": item.id,
                        "question": item.text,
                        "status": "unknown",
                        "reason": "검증 미완료",
                        "evidence_ids": [],
                        "fact_keys": [],
                        "error_code": None,
                    }
                    try:
                        if (
                            eid not in set(research["eligible_evidence_ids"][aid])
                            or eid not in item.evidence_ids
                            or not isinstance(quote, str)
                            or not quote
                            or not isinstance(start, int)
                            or not isinstance(end, int)
                            or start < 0
                            or end <= start
                            or evidence[eid].text[start:end] != quote
                        ):
                            raise LegalGraphError(
                                "checklist-grounding",
                                "요건의 원문 인용·구간·시행일이 일치하지 않습니다.",
                            )
                        result = checked_output(
                            "audit_item",
                            self.model.complete(
                                "audit_item",
                                {
                                    **base,
                                    "item": {
                                        "id": item.id,
                                        "question": item.text,
                                        "quote": quote,
                                        "evidence_id": eid,
                                    },
                                },
                            ),
                        )
                        if not set(result["evidence_ids"]) <= allowed:
                            raise LegalGraphError(
                                "model-grounding", "요건 검증에 허용되지 않은 근거가 사용됐습니다."
                            )
                        if result["status"] != "unknown" and (
                            not result["evidence_ids"]
                            or not result["fact_keys"]
                            or any(
                                key in {"proposed_route", "submitted_lot"}
                                or key not in facts
                                or facts[key] is None
                                or facts[key] == ""
                                for key in result["fact_keys"]
                            )
                        ):
                            raise LegalGraphError(
                                "missing-facts",
                                "판단을 뒷받침하는 입력 사실이 확인되지 않았습니다.",
                            )
                        check.update(result)
                    except LegalGraphError as exc:
                        check.update(error_code=exc.code, reason=str(exc))
                    record["items"].append(check)
                decision = checked_output(
                    "audit_article",
                    self.model.complete("audit_article", {**base, "item_checks": record["items"]}),
                )
                if any(item["error_code"] for item in record["items"]):
                    raise LegalGraphError(
                        "incomplete-item-checks",
                        "오류로 확인하지 못한 요건이 있어 조문 판단을 보류합니다.",
                    )
                if not set(decision["evidence_ids"]) <= allowed:
                    raise LegalGraphError(
                        "model-grounding", "조문 종합 판단의 근거가 허용 범위를 벗어났습니다."
                    )
                checks = {item["item_id"]: item for item in record["items"]}
                decisive = decision["decisive_item_ids"]
                if not set(decisive) <= checks.keys():
                    raise LegalGraphError(
                        "model-grounding", "종합 판단에 존재하지 않는 검증 항목이 사용됐습니다."
                    )
                if decision["status"] != "unknown" and (
                    not decisive
                    or not decision["evidence_ids"]
                    or any(checks[i]["status"] == "unknown" for i in decisive)
                ):
                    raise LegalGraphError(
                        "unknown-decisive-condition",
                        "결정에 필요한 요건의 검증이 완료되지 않았습니다.",
                    )
                record.update(decision)
            except LegalGraphError as exc:
                record.update(status="unknown", error_code=exc.code, reason=str(exc))
            records.append(record)
            {"applicable": verified, "inapplicable": rejected, "unknown": unknown}[
                record["status"]
            ].append(aid)

        # Unknown remains visible for human review. Only explicit inapplicable articles are pruned.
        retained_articles = set(verified + unknown)
        retained_cases = sorted(
            {
                e.source
                for e in self.snapshot.edges
                if e.relation == "CITES"
                and e.source in research["case_ids"]
                and e.target in retained_articles
            }
        )
        verified_cases = sorted(
            {
                e.source
                for e in self.snapshot.edges
                if e.relation == "CITES"
                and e.source in retained_cases
                and e.target in verified
                and e.data.get("version_resolved") is True
            }
        )
        verified_issues = sorted(
            {
                e.target
                for e in self.snapshot.edges
                if (e.relation == "HAS_ISSUE" and e.source in verified_cases)
                or (e.relation == "RELATED_ISSUE" and e.source in verified)
            }
        )
        return {
            "articles": records,
            "verified_article_ids": verified,
            "inapplicable_article_ids": rejected,
            "unknown_article_ids": unknown,
            "retained_case_ids": retained_cases,
            "verified_case_ids": verified_cases,
            "verified_issue_ids": verified_issues,
            "pruned_case_ids": sorted(set(research["case_ids"]) - set(retained_cases)),
            "pruning_scope": "current_result_only_no_database_mutation",
            "coverage_complete": bool(records)
            and not unknown
            and all(r["checklist_exhaustiveness_verified"] for r in records),
            "assessment_scope": "research_applicability_only_not_permit_or_intake_approval",
        }
