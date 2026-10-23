"""Synthetic law/case/model responses: algorithm tests, NOT legal ground truth."""

from datetime import date

from resource_ai.evidence.models import LegalDocument, LegalSection
from resource_ai.legal_graph.builder import build_snapshot
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider


def documents():
    law = LegalDocument(
        source_id="synthetic-law-v1",
        document_type="LAW",
        title="가상 폐자원법",
        source_url="https://www.law.go.kr/example-test-only",
        source_org="가상 시험 자료",
        effective_from=date(2020, 1, 1),
        version="synthetic-v1",
        metadata={"synthetic": True},
        sections=[
            LegalSection(
                heading=f"제{i}조",
                content=f"가상 제{i}조. 허가가 확인된 경우 해당 조건을 검토한다.",
                citation=f"가상법 제{i}조",
                metadata={"article": f"제{i}조"},
            )
            for i in (17, 25, 48)
        ],
    )
    cases = [
        LegalDocument(
            source_id=f"synthetic-case-{i}",
            document_type="PRECEDENT",
            title=f"가상 사례 {i}",
            source_url="https://www.law.go.kr/example-test-only",
            document_no=f"TEST-{i}",
            version="synthetic-v1",
            metadata={"synthetic": True, "decision_date": "2021-01-01"},
            sections=[
                LegalSection(
                    heading="가상 사실",
                    content=f"가상 사업장 {i}의 폐자원 위탁 관련 사례.",
                    citation="가상 사실",
                ),
                LegalSection(
                    heading="참조조문",
                    content=f"가상 폐자원법 제{article}조",
                    citation="가상 참조",
                    metadata={"section_type": "참조조문"},
                ),
            ],
        )
        for i, article in enumerate((17, 25, 48), 1)
    ]
    return [law, *cases]


class SyntheticModel:
    name = "synthetic-scripted-model-no-network"

    def __init__(self):
        self.calls = []
        self.item_status = "met"
        self.article_status = "applicable"
        self.malformed_task = None
        self.foreign_evidence = False
        self.false_quote = False
        self.forbidden_final = False

    def complete(self, task, payload):
        self.calls.append((task, payload))
        if task == self.malformed_task:
            return {"invalid": "fixture"}
        features = {
            "actor": "가상 사업장",
            "conduct": "위탁",
            "object": "폐자원",
            "context": "허가 검토",
        }
        if task == "extract_case":
            return {
                "features": features,
                "issues": ["위탁 책임"],
                "evidence_ids": [payload["evidence"][0]["id"]],
            }
        if task == "extract_checklist":
            span = payload["evidence"][0]
            return {
                "items": [
                    {
                        "question": "허가가 확인됐는가?",
                        "evidence_id": span["id"],
                        "quote": "조작된 인용" if self.false_quote else span["text"],
                    }
                ],
                "issues": ["위탁 책임"],
            }
        if task == "summarize_community":
            return {"summary": "가상 사업장의 폐자원 위탁 허가 검토 사례 묶음"}
        if task == "plan_query":
            return {"features": features, "issues": ["위탁 책임"]}
        if task == "audit_item":
            return {
                "status": self.item_status,
                "reason": "가상 응답: 입력 사실과 가상 조문을 대조함",
                "evidence_ids": ["foreign-evidence"]
                if self.foreign_evidence
                else [payload["item"]["evidence_id"]],
                "fact_keys": ["permit_confirmed"],
            }
        if task == "audit_article":
            return {
                "status": self.article_status,
                "reason": "가상 응답: 항목별 결과와 조문을 종합함",
                "evidence_ids": [payload["evidence"][0]["id"]],
                "decisive_item_ids": [payload["item_checks"][0]["item_id"]],
            }
        if task == "adjudicate":
            article = payload["articles"][0]
            return {
                "summary": "가상 근거에 대한 조사 요약. 실제 허가 승인이 아님.",
                "findings": [
                    {
                        "text": "가상 조문의 조건을 검토함",
                        "article_ids": ["forbidden"] if self.forbidden_final else [article["id"]],
                        "evidence_ids": [article["evidence_ids"][0]],
                        "case_ids": [],
                    }
                ],
                "missing_facts": [],
            }
        raise AssertionError(task)


def enriched():
    embeddings, model = HashEmbeddingProvider(32), SyntheticModel()
    snapshot = build_snapshot(documents(), embeddings, model, dataset_sha256="a" * 64)
    return snapshot, embeddings, model


class MemoryStore:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def load(self):
        return self.snapshot, {
            "generation": "synthetic-generation",
            "fingerprint": self.snapshot.fingerprint(),
        }
