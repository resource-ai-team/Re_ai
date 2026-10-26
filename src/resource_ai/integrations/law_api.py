from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

import httpx


@dataclass(frozen=True)
class LawSearchItem:
    law_id: str
    mst: str
    title: str
    promulgation_date: str
    effective_date: str
    ministry: str | None


@dataclass(frozen=True)
class PrecedentSearchItem:
    precedent_id: str
    case_name: str
    case_number: str
    decision_date: str
    court_name: str


class LawOpenApiClient:
    """Small client for the official 국가법령정보 공동활용 API."""

    def __init__(
        self,
        oc: str,
        base_url: str = "https://www.law.go.kr/DRF",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not oc.strip():
            raise ValueError("LAW_API_OC is required")
        self.oc = oc
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, endpoint: str, **params: Any) -> str:
        response = self._client.get(
            f"{self.base_url}/{endpoint}",
            params={"OC": self.oc, "type": "XML", **params},
        )
        response.raise_for_status()
        return response.text

    def search_laws(self, query: str) -> str:
        return self._get("lawSearch.do", target="law", query=query, display=100)

    def fetch_law(self, law_id: str, article_code: str | None = None) -> str:
        params: dict[str, Any] = {"target": "law", "ID": law_id}
        if article_code:
            params["JO"] = article_code
        return self._get("lawService.do", **params)

    def search_precedents(self, case_number: str) -> str:
        return self._get("lawSearch.do", target="prec", nb=case_number, display=100)

    def fetch_precedent(self, precedent_id: str) -> str:
        return self._get("lawService.do", target="prec", ID=precedent_id)

    def search_effective_laws(self, query: str) -> str:
        """공포 기준 최신법이 아닌 시행일 기준 현행법을 찾는다."""
        return self._get("lawSearch.do", target="eflaw", query=query, nw=3, display=100)

    def fetch_effective_law(self, mst: str, effective_date: str) -> str:
        """목록에서 확인한 시행 버전을 고정해서 가져온다."""
        return self._get("lawService.do", target="eflaw", MST=mst, efYd=effective_date)

    def search_administrative_rules(self, query: str) -> str:
        return self._get("lawSearch.do", target="admrul", query=query, nw=1, display=100)

    def fetch_administrative_rule(self, serial: str) -> str:
        return self._get("lawService.do", target="admrul", ID=serial)
