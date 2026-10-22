"""Public download safety checks with synthetic rows, no live portal dependency."""

import hashlib
import json

import httpx
import pytest

from resource_ai.knowledge_graph.facility_registry import NATIONAL_COLUMNS
from scripts.collect_facility_registry import collect
from tests.unit.test_facility_search import national_row


def client_for(rows, *, declared=None, header_change=None):
    header = {
        "totalCount": len(rows) if declared is None else declared,
        "tableVO": {
            "svcTableNm": "tn_pubr_public_tret_was_svc",
            "colNmList": sorted(NATIONAL_COLUMNS - {"INSTT_CODE", "INSTT_NM"}),
        },
    }
    if header_change:
        header.update(header_change)

    def handler(request):
        if request.url.path == "/download/columList.json":
            return httpx.Response(200, json=header)
        assert request.url.path == "/download/standard.json"
        assert request.url.params["publicDataPk"] == "15114147"
        assert request.url.params["perPage"] == "10000"
        return httpx.Response(200, json=rows)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_public_snapshot_has_verified_count_sha_and_restricted_transfer_manifest(tmp_path):
    path = tmp_path / "registry.json"
    with client_for([national_row()]) as client:
        report = collect(client, path)
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf8"))
    assert report == manifest
    assert report["rows"] == 1
    assert report["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["model_transfer_allowed"] is False
    assert json.loads(path.read_bytes()) == [national_row()]


@pytest.mark.parametrize("case", ["count_mismatch", "duplicates", "wrong_columns", "bad_header"])
def test_changed_or_incomplete_download_writes_no_snapshot(tmp_path, case):
    rows = [national_row()]
    options = {}
    if case == "count_mismatch":
        options["declared"] = 2
    elif case == "duplicates":
        rows *= 2
    elif case == "wrong_columns":
        rows[0]["unexpected"] = "value"
    else:
        options["header_change"] = {"totalCount": 50001}
    path = tmp_path / "registry.json"
    with client_for(rows, **options) as client, pytest.raises(ValueError):
        collect(client, path)
    assert not path.exists()
    assert not path.with_suffix(".manifest.json").exists()


@pytest.mark.parametrize("existing", ["registry.json", "registry.manifest.json"])
def test_existing_snapshot_or_manifest_prevents_network_and_overwrite(tmp_path, existing):
    (tmp_path / existing).write_text("preserve", encoding="utf8")

    def handler(_):
        pytest.fail("No request should be made when an output already exists")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        collect(client, tmp_path / "registry.json")
    assert (tmp_path / existing).read_text(encoding="utf8") == "preserve"
