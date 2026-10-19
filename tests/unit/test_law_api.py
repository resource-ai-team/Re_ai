import httpx

from resource_ai.integrations.law_api import LawOpenApiClient


def test_client_sends_official_api_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/DRF/lawService.do"
        assert request.url.params["OC"] == "test-id"
        assert request.url.params["target"] == "law"
        assert request.url.params["ID"] == "001771"
        assert request.url.params["JO"] == "001700"
        return httpx.Response(200, text="<법령 />")

    client = LawOpenApiClient(
        "test-id",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.fetch_law("001771", "001700") == "<법령 />"
    finally:
        client.close()
