"""Additive internal API contract; no caller-controlled model or database URL."""

import json
from typing import Any

from pydantic import Field, model_validator

from resource_ai.legal_graph.models import Strict


class LegalGraphRequest(Strict):
    query: str = Field(min_length=2, max_length=8000)
    facts: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)
    max_model_calls: int = Field(default=40, ge=1, le=100)
    max_articles: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def bounded_facts(self):
        try:
            encoded = json.dumps(self.facts, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("facts는 유한한 JSON 값이어야 합니다.") from None
        if (
            len(self.facts) > 100
            or len(encoded) > 32000
            or any(len(key) > 100 for key in self.facts)
        ):
            raise ValueError("facts의 크기 한도를 초과했습니다.")
        if "proposed_route" in self.facts:
            raise ValueError("proposed_route는 내부 공정 가설용 예약 필드입니다.")
        return self
