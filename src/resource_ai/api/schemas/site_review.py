"""One internal request for site investigation and grounded scenario review."""

from typing import Literal

from pydantic import Field, StrictBool

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.api.schemas.decision_review import DecisionReviewResponse, NumericBinding
from resource_ai.api.schemas.kg_lci import KGPathLCIResponse
from resource_ai.api.schemas.legal_graph import LegalGraphRequest
from resource_ai.api.schemas.site_discovery import SiteDiscoveryRequest, SiteDiscoveryResponse
from resource_ai.evidence.critic_models import SemanticCriticOptions


class SiteReviewRequest(SiteDiscoveryRequest):
    max_legal_queries: int = Field(default=12, ge=1, le=100)
    legal_top_k: int = Field(default=3, ge=1, le=5)
    max_technical_queries: int = Field(default=6, ge=0, le=50)
    technical_top_k: int = Field(default=3, ge=1, le=5)
    include_lci: StrictBool = True
    numeric: NumericBinding | None = None
    legal_graph: LegalGraphRequest | None = None
    semantic_critic: SemanticCriticOptions | None = None


class SiteReviewResponse(StrictModel):
    contract_version: Literal["site-grounded-review-v1"] = "site-grounded-review-v1"
    site: SiteDiscoveryResponse
    review: DecisionReviewResponse
    lci: KGPathLCIResponse | None = None
    route: Literal["REVIEW", "REVISE"]
    report_markdown: str
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    dispatch_approved: Literal[False] = False
