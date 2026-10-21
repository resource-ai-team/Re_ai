"""The paper's three retrieval branches over a loaded legal graph snapshot."""

import math
from datetime import date

from resource_ai.legal_graph.builder import feature_text
from resource_ai.legal_graph.llm import checked_output
from resource_ai.legal_graph.models import LegalGraphError


def cosine(a, b):
    if not a or len(a) != len(b) or any(not math.isfinite(v) for v in (*a, *b)):
        raise LegalGraphError("embedding-integrity", "임베딩 차원·유한값이 일치하지 않습니다.")
    na, nb = sum(v * v for v in a), sum(v * v for v in b)
    if not na or not nb:
        raise LegalGraphError("embedding-integrity", "비어 있는 임베딩으로 검색하지 않습니다.")
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(na * nb)


class Researcher:
    def __init__(self, snapshot, embeddings, model):
        self.snapshot, self.embeddings, self.model = snapshot, embeddings, model

    def retrieve(self, query, facts, *, top_k=5):
        if not 1 <= top_k <= 20:
            raise LegalGraphError("invalid-input", "top_k는 1~20입니다.")
        if self.snapshot.metadata.get("enrichment_complete") is not True:
            raise LegalGraphError(
                "graph-not-enriched",
                "원문 구조만 적재되어 있습니다. 모델로 특징·요건·커뮤니티를 생성해야 합니다.",
            )
        declared_model = self.snapshot.metadata.get("embedding_model")
        current_model = getattr(self.embeddings, "model_name", type(self.embeddings).__name__)
        if declared_model and declared_model != current_model:
            raise LegalGraphError(
                "embedding-integrity", "그래프 적재와 조회의 임베딩 모델이 다릅니다."
            )
        try:
            as_of = date.fromisoformat(str(facts.get("as_of", date.today().isoformat())))
        except ValueError:
            raise LegalGraphError("invalid-input", "as_of는 ISO 날짜여야 합니다.") from None
        plan = checked_output(
            "plan_query", self.model.complete("plan_query", {"query": query, "facts": facts})
        )
        vector = self.embeddings.embed_query(feature_text(plan["features"]))
        nodes, evidence = self.snapshot.node_map(), self.snapshot.evidence_map()

        def current(node):
            if node.data.get("is_current") is False:
                return False
            start, end = node.data.get("decision_date"), node.data.get("effective_until")
            try:
                return (not start or date.fromisoformat(start) <= as_of) and (
                    not end or as_of <= date.fromisoformat(end)
                )
            except (TypeError, ValueError):
                raise LegalGraphError(
                    "graph-integrity", "법률 그래프의 사례 기준일이 올바르지 않습니다."
                ) from None

        def rank(items, target):
            return sorted(
                ((cosine(target, n.vector), n.id) for n in items), key=lambda r: (-r[0], r[1])
            )

        cases = [n for n in nodes.values() if n.kind == "Case" and current(n)]
        valid_cases = {n.id for n in cases}
        semantic_scores = rank(cases, vector)[:top_k]
        semantic = [nid for _, nid in semantic_scores]
        communities = [
            n
            for n in nodes.values()
            if n.kind == "Community"
            and any(
                e.source in valid_cases and e.target == n.id and e.relation == "BELONGS_TO"
                for e in self.snapshot.edges
            )
        ]
        community_scores = rank(communities, vector)
        selected_community = community_scores[0][1] if community_scores else None
        members = {
            e.source
            for e in self.snapshot.edges
            if e.relation == "BELONGS_TO" and e.target == selected_community
        }
        community_case_scores = rank([n for n in cases if n.id in members], vector)[:top_k]
        community_cases = [nid for _, nid in community_case_scores]
        issues = [n for n in nodes.values() if n.kind == "Issue"]
        anchored, anchor_scores = set(), []
        for label in plan["issues"]:
            scores = rank(issues, self.embeddings.embed_query(label))[:top_k]
            anchored.update(nid for _, nid in scores)
            anchor_scores.append(
                {
                    "predicted_issue": label,
                    "matches": [{"id": nid, "score": score} for score, nid in scores],
                }
            )
        anchored_cases = sorted(
            {
                e.source
                for e in self.snapshot.edges
                if e.relation == "HAS_ISSUE" and e.target in anchored and e.source in valid_cases
            }
        )
        all_cases = sorted(set(semantic + community_cases + anchored_cases))
        candidates = sorted(
            {
                e.target
                for e in self.snapshot.edges
                if e.source in all_cases
                and e.relation == "CITES"
                and nodes[e.target].kind == "Article"
            }
        )
        allowed_evidence, articles, removed = {}, [], []
        for aid in candidates:
            article = nodes[aid]
            spans = [
                eid
                for eid in article.evidence_ids
                if evidence[eid].effective_from is None or evidence[eid].effective_from <= as_of
            ]
            until = article.data.get("effective_until")
            start = article.data.get("effective_from")
            if (
                spans
                and article.data.get("is_current") is not False
                and (not until or as_of.isoformat() <= until)
                and (not start or start <= as_of.isoformat())
            ):
                articles.append(aid)
                allowed_evidence[aid] = spans
            else:
                removed.append(aid)
        used_issues = sorted(
            {
                e.target
                for e in self.snapshot.edges
                if e.source in all_cases and e.relation == "HAS_ISSUE"
            }
        )
        return {
            "query_features": plan["features"],
            "as_of": as_of.isoformat(),
            "case_ids": all_cases,
            "article_ids": articles,
            "issue_ids": used_issues,
            "eligible_evidence_ids": allowed_evidence,
            "future_or_noncurrent_article_ids": removed,
            "strategies": {
                "semantic": {
                    "case_ids": semantic,
                    "scores": [{"id": nid, "score": score} for score, nid in semantic_scores],
                },
                "community": {
                    "community_id": selected_community,
                    "case_ids": community_cases,
                    "selection": "single_most_similar_community",
                },
                "issue_anchor": {
                    "predicted_issues": plan["issues"],
                    "issue_ids": sorted(anchored),
                    "case_ids": anchored_cases,
                    "matches": anchor_scores,
                },
            },
            "limitations": [
                "한국 규제 쟁점을 원논문의 죄명 앵커 대신 사용합니다.",
                "검색 연결은 조문의 법적 적용·과거 법령 버전·현행 허가를 입증하지 않습니다.",
                "등록 판례 수가 적으면 커뮤니티 검색의 효과를 검증할 수 없습니다.",
            ],
            "registered_case_count": len(cases),
        }
