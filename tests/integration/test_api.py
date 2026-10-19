from datetime import date

from fastapi.testclient import TestClient

from resource_ai.core.dependencies import get_repository
from resource_ai.evidence.models import SearchResult
from resource_ai.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class StubRepository:
    def search(self, query: str, top_k: int) -> list[SearchResult]:
        return [
            SearchResult(
                chunk_id=1,
                title="폐기물관리법",
                document_type="LAW",
                heading="제17조",
                content="공식 원문",
                citation="폐기물관리법 제17조",
                source_url="https://www.law.go.kr/법령/폐기물관리법",
                effective_from=date(2026, 9, 18),
                score=0.03,
                retrieval_method="keyword+vector+reranked",
            )
        ]


def test_search_returns_hybrid_retrieval_metadata() -> None:
    app.dependency_overrides[get_repository] = StubRepository
    try:
        response = TestClient(app).get("/rag/search", params={"q": "위탁 책임"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["retrieval_method"] == "keyword+vector+reranked"

