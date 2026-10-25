from types import SimpleNamespace

from fastapi.testclient import TestClient

from resource_ai.core.dependencies import get_graph_query, get_repository
from resource_ai.evidence.models import SearchGroup, SearchResult
from resource_ai.main import app

KG_EVIDENCE_ID = "kg-evidence:paper-1"


class StubGraph:
    def candidate_pool(self, request):
        return SimpleNamespace(
            graph_trace={"generation": "g1"},
            items=[
                SimpleNamespace(
                    candidate_id="kg-candidate-1",
                    kind="literature",
                    research_packet={
                        "source_record": {
                            "metadata": {
                                "waste": "폐PP",
                                "transforming_process": "압출",
                            }
                        },
                        "evidence": [
                            {
                                "evidence_id": KG_EVIDENCE_ID,
                                "properties": {
                                    "reference_raw": "10.1000/example.1",
                                    "doi_candidates": ["10.1000/example.1"],
                                },
                                "source": {"metadata": {}},
                            }
                        ],
                    },
                )
            ],
            pagination=SimpleNamespace(next_cursor=None, has_more=False),
        )


class StubRepository:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k, *, search_group):
        self.calls.append((query, top_k, search_group))
        return [
            SearchResult(
                chunk_id=7,
                title="검수된 기술문헌",
                document_type="TECHNICAL_LITERATURE",
                heading="적용 범위",
                content="KG 주장을 지지하는 검수 근거",
                citation="p. 3",
                source_url="https://doi.org/10.1000/example.1",
                effective_from=None,
                score=0.9,
                retrieval_method="hybrid+reranked",
                metadata={
                    "chunk": {
                        "kg_evidence_ids": [KG_EVIDENCE_ID],
                        "kg_claim_status": "SUPPORTED",
                    }
                },
            )
        ]


def test_reference_validation_connects_kg_and_technical_rag():
    repository = StubRepository()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = StubGraph
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        response = TestClient(app).post(
            "/analysis/reference-validation",
            json={"query": {"material": "PP", "page_size": 10}},
        )
    finally:
        app.dependency_overrides = previous

    assert response.status_code == 200
    body = response.json()
    evidence = body["candidates"][0]["evidence"][0]
    assert body["route"] == "PASS"
    assert evidence["kg_evidence_id"] == KG_EVIDENCE_ID
    assert evidence["rag_chunk_ids"] == [7]
    assert evidence["rag_source_urls"] == ["https://doi.org/10.1000/example.1"]
    assert evidence["citations"] == ["p. 3"]
    assert repository.calls[0][2] == SearchGroup.TECHNICAL
