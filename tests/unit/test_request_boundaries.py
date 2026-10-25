import pytest
from fastapi.testclient import TestClient

from resource_ai.main import app


@pytest.mark.parametrize(
    "path,wrap",
    [
        ("/kg/candidates", False),
        ("/kg/candidate-pool", False),
        ("/kg/process-plans", False),
        ("/analysis/review-template", True),
        ("/analysis/kg-lci", True),
    ],
)
@pytest.mark.parametrize("value", [True, False, 0, -1, 1e100])
def test_invalid_mass_never_reaches_graph_or_arithmetic(path, wrap, value):
    lot = {"material": "PP", "quantity_kg": value}
    response = TestClient(app).post(path, json={"lot": lot} if wrap else lot)
    assert response.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_raw_json_returns_validation_error_instead_of_server_error(value):
    response = TestClient(app, raise_server_exceptions=False).post(
        "/kg/process-plans",
        content='{"material":"PP","quantity_kg":' + value + "}",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert all("input" not in e and "ctx" not in e for e in response.json()["detail"])


def test_validation_response_does_not_echo_company_field_values():
    secret = "SYNTHETIC_PRIVATE_REPORT_VALUE_DO_NOT_REFLECT"
    response = TestClient(app).post("/kg/process-plans", json={"quantity_kg": secret})
    assert response.status_code == 422 and secret not in response.text
