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
    def __init__(self) -> None:
        self.calls = []

    def search(self, query: str, top_k: int, **kwargs) -> list[SearchResult]:
        self.calls.append((query, top_k, kwargs))
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


def test_search_maps_legal_group_to_repository() -> None:
    repository = StubRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        response = TestClient(app).get(
            "/rag/search",
            params={"q": "위탁 책임", "search_group": "LEGAL"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.calls[0][2] == {"search_group": "LEGAL", "tenant_id": None}


def test_company_search_requires_tenant_id() -> None:
    repository = StubRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        response = TestClient(app).get(
            "/rag/search",
            params={"q": "성분 분석", "search_group": "COMPANY"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "tenant_id is required for COMPANY search"
    assert repository.calls == []


def test_company_search_passes_tenant_scope() -> None:
    repository = StubRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        response = TestClient(app).get(
            "/rag/search",
            params={
                "q": "성분 분석",
                "search_group": "COMPANY",
                "tenant_id": "tenant-a",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.calls[0][2] == {"search_group": "COMPANY", "tenant_id": "tenant-a"}


def test_search_rejects_unknown_group() -> None:
    app.dependency_overrides[get_repository] = StubRepository
    try:
        response = TestClient(app).get(
            "/rag/search",
            params={"q": "성분 분석", "search_group": "UNKNOWN"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
