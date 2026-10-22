"""Source-bound KG → LCI tests using curated synthetic KG and LCIA fixture."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.kg_lci import KGPathLCIIntegrityError, KGPathLCIService
from resource_ai.api.routes.kg_lci import get_lci_datasets
from resource_ai.api.schemas.facility_assessment import AssessedPlansResponse
from resource_ai.api.schemas.kg_lci import KGPathLCIRequest
from resource_ai.api.schemas.kg_planning import PathPlanningResponse, PlanningPagination
from resource_ai.calculators.lci import LCIDataset
from resource_ai.calculators.lci.evaluate import PET_FLAKE_UUID, PET_INCINERATION_UUID
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph.planning import enumerate_paths, make_plan
from resource_ai.knowledge_graph.planning_reference import reference
from resource_ai.main import app


class FakePetGraph:
    """Replicate authenticated/internal ProcessPlan pages without running Neo4j."""

    def __init__(self, *, change_graph=False, truncate=False):
        self.calls = []
        self.change_graph = change_graph
        self.truncate = truncate
        value = reference()
        paths, _ = enumerate_paths(value, "pet", "bottle", None, 6)
        self.plans = [
            make_plan(value, item, audience="internal", assumed_form=False)
            for item in paths.paths
        ]
        assert len(self.plans) >= 2

    def assessed_plans(self, request):
        self.calls.append(request)
        offset = int(request.cursor or 0)
        selection = self.plans[offset : offset + request.page_size]
        end = offset + len(selection)
        trace = {"generation": "test-2" if self.change_graph and offset else "test-1"}
        return AssessedPlansResponse(
            planning=PathPlanningResponse(
                status="plans",
                normalized_material="pet",
                input_context={},
                missing_fields=[],
                items=selection,
                pagination=PlanningPagination(
                    enumerated_plans=len(self.plans),
                    returned_count=len(selection),
                    has_more=end < len(self.plans),
                    next_cursor=str(end) if end < len(self.plans) else None,
                ),
                graph_trace=trace,
                search_scope={"all_paths_enumerated": not self.truncate},
                limitations=[],
            ),
            assessments=[
                {"plan_id": plan.plan_id, "status": "needs_verification", "stages": []}
                for plan in selection
            ],
            limitations=[],
        )


def fake_dataset(process_uuid=PET_FLAKE_UUID, climate="0.57164725210341"):
    return LCIDataset(
        dataset_key="lci-2023-168",
        filename="fixture.zip",
        source_sha256="a" * 64,
        process_uuid=process_uuid,
        name="synthetic-source-metadata",
        year=2022,
        country="KR",
        valid_until=2025,
        reference_flow_id="synthetic-flow",
        reference_flow_name="recycled PET flakes",
        reference_direction="Output" if process_uuid == PET_FLAKE_UUID else "Input",
        reference_quantity=Decimal(1),
        reference_unit="kg",
        climate_change_amount=Decimal(climate),
        climate_method_uuid="test-method",
        climate_unit_status="LCIA_METHOD_UNIT_NOT_IN_ARCHIVE",
        waste_pet_input_per_ref=Decimal("1.27949") if process_uuid == PET_FLAKE_UUID else None,
    )


def request(**lot):
    return KGPathLCIRequest(lot={"material": "PET", "input_form": "bottle", "page_size": 1, **lot})


def test_all_kg_pages_preserved_aggregate_once_no_pellet_factor():
    graph = FakePetGraph()
    response = KGPathLCIService(graph, [fake_dataset()]).run(request(quantity_kg=1000))
    rows = response.results
    assert len(graph.calls) == len(rows) == 2
    assert response.coverage["complete_within_curated_scope"] is True
    assert response.coverage["next_cursor"] is None
    assert {r["plan_id"] for r in rows} == {p.plan_id for p in graph.plans}
    mapped = [r for r in rows if r["calculation_status"] == "ESTIMATED_REVIEW_REQUIRED"]
    unmatched = [r for r in rows if r["calculation_status"] == "UNMATCHED"]
    assert len(mapped) == len(unmatched) == 1
    expected = Decimal(1000) / Decimal("1.27949") * Decimal("0.57164725210341")
    assert Decimal(mapped[0]["carbon_kgco2e"]) == expected
    assert mapped[0]["lci_mapping_type"] == "AGGREGATED"
    assert len(mapped[0]["stage_ids"]) == 3
    assert len(unmatched[0]["stage_ids"]) == 4
    assert unmatched[0]["carbon_kgco2e"] is None
    assert all(not r["recommended"] and not r["comparable"] for r in rows)
    assert response.route == "REVIEW" and response.final_recommendations == []


def test_missing_or_unconfirmed_lot_blocks_value():
    graph = FakePetGraph()
    missing_mass = KGPathLCIService(graph, [fake_dataset()]).run(request())
    assert all(r["calculation_status"] == "UNAVAILABLE" for r in missing_mass.results)
    assert all(r["carbon_kgco2e"] is None for r in missing_mass.results)
    unconfirmed = KGPathLCIService(FakePetGraph(), [fake_dataset()]).run(
        request(quantity_kg=1000, input_form="bale")
    )
    # This fake KG deliberately uses bottle-start plans. The only source of
    # feedstock confirmation is still the user's lot, never the plan label.
    assert all(r["carbon_kgco2e"] is None for r in unconfirmed.results)


def test_no_lci_zip_is_unavailable_not_zero():
    res = KGPathLCIService(FakePetGraph(), []).run(request(quantity_kg=1000))
    assert len(res.results) == 2
    assert all(r["calculation_status"] == "UNAVAILABLE" for r in res.results)
    assert res.datasets_loaded == []


def test_installed_unrelated_lci_does_not_make_up_missing_pet_coefficient():
    res = KGPathLCIService(
        FakePetGraph(), [fake_dataset(process_uuid=PET_INCINERATION_UUID, climate="2.454")]
    ).run(request(quantity_kg=1000))
    assert all(r["carbon_kgco2e"] is None for r in res.results)


def test_page_cap_and_exhaustion_never_claim_full_coverage():
    limited = KGPathLCIService(FakePetGraph(), [fake_dataset()]).run(
        KGPathLCIRequest(lot=request(quantity_kg=1000).lot, max_pages=1)
    )
    assert limited.coverage["collected_candidates"] == 1
    assert limited.coverage["enumerated_in_graph"] == 2
    assert not limited.coverage["complete_within_curated_scope"]
    assert limited.coverage["next_cursor"] is not None

    clipped = KGPathLCIService(FakePetGraph(truncate=True), [fake_dataset()]).run(
        request(quantity_kg=1000)
    )
    assert clipped.coverage["all_pages_read"] is True
    assert clipped.coverage["complete_within_curated_scope"] is False


def test_changed_graph_generation_rejected():
    with pytest.raises(KGPathLCIIntegrityError, match="Graph generation"):
        KGPathLCIService(FakePetGraph(change_graph=True), [fake_dataset()]).run(
            request(quantity_kg=1000)
        )


def test_input_cursor_and_external_audience_rejected():
    client = TestClient(app)
    invalid = {
        "lot": {
            "material": "PET", "input_form": "bottle", "quantity_kg": 1000,
            "cursor": "not-first-page"
        }
    }
    assert client.post("/analysis/kg-lci", json=invalid).status_code == 422
    invalid["lot"]["cursor"] = None
    invalid["lot"]["audience"] = "external_model"
    assert client.post("/analysis/kg-lci", json=invalid).status_code == 422


def test_api_reads_actual_kg_plan_contract_and_does_not_require_legal_rag():
    graph = FakePetGraph()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: graph
    app.dependency_overrides[get_lci_datasets] = lambda: [fake_dataset()]
    try:
        result = TestClient(app).post(
            "/analysis/kg-lci",
            json={
                "lot": {
                    "material": "PET", "input_form": "bottle",
                    "quantity_kg": 1000, "page_size": 1
                }
            },
        )
    finally:
        app.dependency_overrides = previous
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["contract_version"] == "kg-path-lci-screening-v1"
    assert body["coverage"]["complete_within_curated_scope"] is True
    assert len(body["results"]) == 2
    assert body["route"] == "REVIEW"
    assert body["dispatch_approved"] is False
    assert body["final_recommendations"] == []
