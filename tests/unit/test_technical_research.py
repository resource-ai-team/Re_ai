from dataclasses import replace

from resource_ai.agents.decision_critic import inspect_review
from resource_ai.agents.research import CandidateResearchService, retrieve
from resource_ai.api.schemas.analysis import ResearchRequest
from tests.unit.test_candidate_research import FakeGraph, FakeRepository


class Repositories(FakeRepository):
    def __init__(self, technical_type="TECHNICAL_LITERATURE"):
        super().__init__()
        self.technical_calls = []
        self.technical_type = technical_type

    def search_technical(self, query, top_k):
        self.technical_calls.append((query, top_k))
        return [
            replace(
                super().search(query, top_k)[0],
                document_type=self.technical_type,
                version="source-v2",
                page_no=4,
                source_position="Table 2",
            )
        ]


def test_optional_technical_evidence_is_deduplicated_bound_and_not_legal_approval():
    repo = Repositories()
    result = CandidateResearchService(FakeGraph(), repo).run(
        ResearchRequest(
            lot={"material": "PP", "input_form": "scrap"},
            max_technical_queries=2,
        )
    )
    assert len(repo.technical_calls) == 2
    assert len(result.candidates) == 3
    assert any(s.status == "budget_exhausted" for s in result.technical_searches)
    assert all(c.technical_tasks for c in result.candidates)
    hit = result.technical_searches[0].results[0]
    assert (hit["version"], hit["page_no"], hit["source_position"]) == ("source-v2", 4, "Table 2")
    assert hit["applicability"] == "not_audited"
    assert not result.coverage["technical_applicability_audited"]
    assert not result.final_recommendations and not result.dispatch_approved


def test_company_or_law_results_cannot_be_mislabelled_as_technical_evidence():
    for kind in ["LAW", "COMPANY_DOCUMENT"]:
        status, rows = retrieve(Repositories(kind), "public process query", 3, technical=True)
        assert status == "unavailable" and rows == []


def test_old_callers_do_not_trigger_technical_search():
    repo = Repositories()
    result = CandidateResearchService(FakeGraph(), repo).run(
        ResearchRequest(lot={"material": "PP"})
    )
    assert repo.technical_calls == [] and result.technical_searches == []
    assert result.coverage["technical_requested"] is False


def test_technical_search_gaps_and_unaudited_hits_become_stage_risks():
    result = CandidateResearchService(FakeGraph(), Repositories()).run(
        ResearchRequest(lot={"material": "PP", "input_form": "scrap"}, max_technical_queries=1)
    )
    rows = [r for r in inspect_review(result, None, None) if r["code"].startswith("technical_")]
    assert {r["code"] for r in rows} == {
        "technical_evidence_missing",
        "technical_applicability_unverified",
    }
    assert all(r["severity"] == "missing" and r["target_type"] == "stage" for r in rows)
    assert any(r["evidence_ids"] for r in rows)
