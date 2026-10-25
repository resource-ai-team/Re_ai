from types import SimpleNamespace

from resource_ai.agents.evidence_validation import ReferenceValidationService
from resource_ai.api.schemas.evidence_validation import ReferenceValidationRequest
from resource_ai.evidence.models import SearchGroup, SearchResult

KG_EVIDENCE_ID = "kg-evidence:paper-1"
DOI = "10.1000/example.1"


def candidate(evidence=None):
    return SimpleNamespace(
        candidate_id="kg-candidate-1",
        kind="literature",
        research_packet={
            "source_record": {
                "metadata": {
                    "waste": "폐PP",
                    "transforming_process": "선별, 압출",
                    "transformed_resource": "재생 펠렛",
                }
            },
            "evidence": evidence
            if evidence is not None
            else [
                {
                    "evidence_id": KG_EVIDENCE_ID,
                    "properties": {
                        "reference_raw": DOI,
                        "doi_candidates": [DOI],
                        "locator": "W2RKG row 1",
                        "article_support_verified": False,
                    },
                    "source": {"metadata": {}},
                }
            ],
        },
    )


class FakeGraph:
    def __init__(self, item=None):
        self.item = item or candidate()

    def candidate_pool(self, request):
        return SimpleNamespace(
            graph_trace={"generation": "g1"},
            items=[self.item],
            pagination=SimpleNamespace(next_cursor=None, has_more=False),
        )


class FakeRepository:
    def __init__(self, *, claim_status=None):
        self.claim_status = claim_status
        self.calls = []

    def search(self, query, top_k, *, search_group):
        self.calls.append((query, top_k, search_group))
        metadata = {"doi": DOI}
        if self.claim_status:
            metadata.update(
                {
                    "kg_evidence_ids": [KG_EVIDENCE_ID],
                    "kg_claim_status": self.claim_status,
                }
            )
        return [
            SearchResult(
                chunk_id=7,
                title="검수된 기술문헌 요약",
                document_type="TECHNICAL_LITERATURE",
                heading="적용 범위",
                content="테스트 근거",
                citation="p. 3",
                source_url=f"https://doi.org/{DOI}",
                effective_from=None,
                score=0.9,
                retrieval_method="hybrid+reranked",
                metadata={"chunk": metadata},
            )
        ]


def request(**changes):
    values = {
        "query": {"material": "PP", "page_size": 10},
        "max_critic_retries": 1,
    }
    values.update(changes)
    return ReferenceValidationRequest.model_validate(values)


def test_explicit_reviewed_link_can_pass_with_traceable_ids():
    repository = FakeRepository(claim_status="SUPPORTED")
    result = ReferenceValidationService(FakeGraph(), repository).run(request())

    assert result.route == "PASS"
    assessment = result.candidates[0].evidence[0]
    assert assessment.status == "SUPPORTED"
    assert assessment.kg_evidence_id == KG_EVIDENCE_ID
    assert assessment.rag_chunk_ids == [7]
    assert assessment.citations == ["p. 3"]
    assert len(repository.calls) == 1
    assert repository.calls[0][2] == SearchGroup.TECHNICAL


def test_doi_match_without_reviewed_link_stays_unknown_and_retry_is_bounded():
    repository = FakeRepository()
    result = ReferenceValidationService(FakeGraph(), repository).run(request())

    assert result.route == "REVIEW"
    assessment = result.candidates[0].evidence[0]
    assert assessment.status == "UNKNOWN"
    assert "same_reference_not_semantically_audited" in assessment.reasons
    assert len(repository.calls) == 2
    assert len(result.searches) == 2


def test_explicit_conflict_routes_to_revise():
    result = ReferenceValidationService(FakeGraph(), FakeRepository(claim_status="CONFLICT")).run(
        request()
    )

    assert result.route == "REVISE"
    assert result.candidates[0].status == "CONFLICT"


def test_candidate_without_reference_never_passes():
    item = candidate(evidence=[])
    result = ReferenceValidationService(FakeGraph(item), FakeRepository()).run(request())

    assert result.route == "REVIEW"
    assert result.candidates[0].status == "UNKNOWN"
    assert result.searches == []
