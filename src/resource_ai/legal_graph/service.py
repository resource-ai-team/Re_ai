"""LegalGraphRAG orchestration; unknown evidence never becomes an approval."""

from resource_ai.legal_graph.auditor import Auditor
from resource_ai.legal_graph.llm import PROMPT_VERSION, BudgetModel, checked_output
from resource_ai.legal_graph.models import LegalGraphError
from resource_ai.legal_graph.researcher import Researcher


def adjudicate(snapshot, model, query, facts, research, audit):
    nodes, evidence = snapshot.node_map(), snapshot.evidence_map()
    article_ids = set(audit["verified_article_ids"])
    base = {
        "status": "review_required",
        "summary": "검증된 적용 근거가 부족하여 판단을 보류합니다.",
        "findings": [],
        "missing_facts": [],
        "error_code": None,
    }
    if not article_ids:
        return {**base, "error_code": "no-verified-articles"}
    # No rejected/unknown article, case or interpretation is sent to the final model.
    article_evidence = {aid: set(research["eligible_evidence_ids"][aid]) for aid in article_ids}
    for record in audit["articles"]:
        if record["article_id"] in article_ids:
            article_evidence[record["article_id"]].update(record["evidence_ids"])
    allowed = set().union(*article_evidence.values())
    case_evidence = {
        cid: {
            eid
            for eid in nodes[cid].evidence_ids
            if evidence[eid].effective_from is None
            or evidence[eid].effective_from.isoformat() <= research["as_of"]
        }
        for cid in audit["verified_case_ids"]
    }
    allowed.update(eid for ids in case_evidence.values() for eid in ids)
    payload = {
        "query": query,
        "facts": facts,
        "as_of": research["as_of"],
        "articles": [
            {"id": aid, "label": nodes[aid].label, "evidence_ids": sorted(article_evidence[aid])}
            for aid in sorted(article_ids)
        ],
        "cases": [
            {"id": cid, "label": nodes[cid].label, "evidence_ids": sorted(case_evidence[cid])}
            for cid in audit["verified_case_ids"]
        ],
        "issues": [{"id": iid, "label": nodes[iid].label} for iid in audit["verified_issue_ids"]],
        "evidence": [evidence[eid].model_dump(mode="json") for eid in sorted(allowed)],
        "limitations": [
            "요건 추출의 완전성은 별도 전문가 검수가 필요합니다.",
            "법적 적용 조사이며 시설 허가·수탁 승인·최종 공정 추천이 아닙니다.",
        ],
    }
    try:
        result = checked_output("adjudicate", model.complete("adjudicate", payload))
        for finding in result["findings"]:
            aids, eids = set(finding["article_ids"]), set(finding["evidence_ids"])
            cids = set(finding["case_ids"])
            if not aids <= article_ids or not eids <= allowed or not cids <= case_evidence.keys():
                raise LegalGraphError(
                    "model-grounding", "최종 판단이 미검증 조문·근거를 인용했습니다."
                )
            if (
                not eids
                <= set().union(
                    *(article_evidence[aid] for aid in aids), *(case_evidence[cid] for cid in cids)
                )
                or any(not (eids & article_evidence[aid]) for aid in aids)
                or any(not (eids & case_evidence[cid]) for cid in cids)
            ):
                raise LegalGraphError(
                    "model-grounding", "최종 판단의 조문과 인용 근거가 연결되지 않습니다."
                )
        return {**base, **result, "status": "grounded_research_summary"}
    except LegalGraphError as exc:
        return {**base, "error_code": exc.code, "summary": str(exc)}


class LegalGraphService:
    def __init__(self, store, embeddings, model):
        self.store, self.embeddings, self.model = store, embeddings, model

    def pin(self):
        """One legal generation for all process candidates in a single analysis."""
        snapshot, trace = self.store.load()

        class PinnedStore:
            def load(self):
                return snapshot, trace

        return LegalGraphService(PinnedStore(), self.embeddings, self.model)

    def run(self, query, facts, *, top_k=5, max_model_calls=40, max_articles=20):
        snapshot, trace = self.store.load()
        bounded = BudgetModel(self.model, max_model_calls)
        research = Researcher(snapshot, self.embeddings, bounded).retrieve(
            query, facts, top_k=top_k
        )
        audit = Auditor(snapshot, bounded).audit(research, facts, max_articles=max_articles)
        conclusion = adjudicate(snapshot, bounded, query, facts, research, audit)
        return {
            "contract_version": "legal-hierargraph-v1",
            "graph_trace": trace,
            "model": bounded.name,
            "prompt_version": PROMPT_VERSION,
            "model_calls": bounded.calls,
            "execution_trace": bounded.trace,
            "research": research,
            "audit": audit,
            "conclusion": conclusion,
            "route": "REVIEW",
            "dispatch_approved": False,
            "recommended": False,
            "limitations": [
                *research["limitations"],
                "사용자 입력은 이 실행에서만 사용하며 그래프에 저장하지 않습니다.",
                "모델 응답·원문 인용 검증은 법률 전문가의 적용성 검토를 대체하지 않습니다.",
                "별표 구조·역사 법령·공식 해석·시설 허가의 누락은 추가 확보가 필요합니다.",
            ],
        }


def configured_service():
    from resource_ai.core.config import get_settings
    from resource_ai.legal_graph.llm import configured_model
    from resource_ai.legal_graph.store import LegalGraphStore
    from resource_ai.rag.retrieval.embeddings import create_embedding_provider

    config = get_settings()
    model = configured_model()
    try:
        embeddings = create_embedding_provider(
            config.embedding_provider, config.embedding_model, config.embedding_dimension
        )
    except (RuntimeError, ValueError, OSError):
        raise LegalGraphError(
            "embedding-unavailable", "법률 그래프의 로컬 임베딩을 준비하지 못했습니다."
        ) from None
    return LegalGraphService(
        LegalGraphStore(namespace=config.legal_graph_namespace), embeddings, model
    )
