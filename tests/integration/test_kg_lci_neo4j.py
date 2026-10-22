"""Real Neo4j KG path → LCI screening API boundary with synthetic LCIA metadata.

Checks graph-generation and process-path wiring, not real-world LCI accuracy.
"""

import os
import sqlite3
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.routes.kg_lci import get_lci_datasets
from resource_ai.calculators.lci import LCIDataset
from resource_ai.calculators.lci.evaluate import PET_FLAKE_UUID
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true",
    reason="Actual Neo4j KG required",
)


@pytest.fixture(scope="module")
def actual_pet_graph():
    config = replace(store.settings(), namespace="resource-lci-test-" + uuid4().hex[:10])
    staging = sqlite3.connect(":memory:")
    build_stage(staging, [], include_curated=True)
    store.import_graph(staging, config=config)
    try:
        yield GraphQuery(config)
    finally:
        assert config.namespace.startswith("resource-lci-test-")
        with store.get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$space DETACH DELETE n",
                space=config.namespace,
            ).consume()
        staging.close()


def synthetic_pet_flake():
    return LCIDataset(
        dataset_key="synthetic-pet-flake",
        filename="no-licensed-data-in-ci.zip",
        source_sha256="a" * 64,
        process_uuid=PET_FLAKE_UUID,
        name="synthetic-kg-integration",
        year=2022,
        country="KR",
        valid_until=2025,
        reference_flow_id="test-flow",
        reference_flow_name="recycled flakes",
        reference_direction="Output",
        reference_quantity=Decimal(1),
        reference_unit="kg",
        climate_change_amount=Decimal("0.57164725210341"),
        climate_method_uuid="fixture-gwp",
        climate_unit_status="LCIA_METHOD_UNIT_NOT_IN_ARCHIVE",
        waste_pet_input_per_ref=Decimal("1.27949"),
    )


def test_actual_neo4j_pet_bottle_path_maps_one_aggregate_lci(actual_pet_graph):
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: actual_pet_graph
    app.dependency_overrides[get_lci_datasets] = lambda: [synthetic_pet_flake()]
    try:
        response = TestClient(app).post(
            "/analysis/kg-lci",
            json={
                "lot": {
                    "material": "PET",
                    "input_form": "bottle",
                    "quantity_kg": 1000,
                    "page_size": 1,
                }
            },
        )
    finally:
        app.dependency_overrides = previous
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["graph_trace"]["reference_version"] == "planning-reference-v2"
    assert data["coverage"]["complete_within_curated_scope"] is True
    assert data["coverage"]["pages_read"] >= 2
    assert data["coverage"]["enumerated_in_graph"] == len(data["results"])
    assert len(data["results"]) >= 2
    estimated = [
        row for row in data["results"] if row["calculation_status"] == "ESTIMATED_REVIEW_REQUIRED"
    ]
    unmatched = [row for row in data["results"] if row["calculation_status"] == "UNMATCHED"]
    assert len(estimated) == 1
    assert unmatched
    assert len(estimated[0]["stage_ids"]) == 3
    assert estimated[0]["lci_mapping_type"] == "AGGREGATED"
    assert estimated[0]["facility_assessment_status"] == "needs_verification"
    assert estimated[0]["recommended"] is False
    assert all(row["dispatch_approved"] is False for row in data["results"])
    assert data["final_recommendations"] == []
