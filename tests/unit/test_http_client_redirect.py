"""GatewayHttpClient must follow gateway redirects without losing method/body."""

from __future__ import annotations

import httpx
import pytest

from app.integrations.http_client import GatewayHttpClient


@pytest.mark.asyncio
async def test_post_follows_http_to_https_redirect_preserving_method_and_body(monkeypatch):
    monkeypatch.setattr("app.integrations.http_client.get_ushspa_token", lambda: "t")
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content))
        if request.url.scheme == "http":
            return httpx.Response(
                301, headers={"location": str(request.url).replace("http://", "https://", 1)}
            )
        return httpx.Response(200, json={"ok": True})

    client = GatewayHttpClient(
        base_url="http://gw.example.com", service_path="/uauth", service_name="ushauth"
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await client.post("/api/v1/create-appointment-cache/", json={"a": 1})

    assert result == {"ok": True}
    assert [s[0] for s in seen] == ["POST", "POST"]
    assert seen[1][1] == "https://gw.example.com/uauth/api/v1/create-appointment-cache/"
    assert seen[1][2] == seen[0][2] != b""
    # base URL upgraded so subsequent calls don't redirect again
    assert client._base_url == "https://gw.example.com"


def test_build_headers_includes_x_forwarded_proto(monkeypatch):
    monkeypatch.setattr("app.integrations.http_client.get_ushspa_token", lambda: "sec-token")
    client = GatewayHttpClient(
        base_url="http://ushspa-kong:8000", service_path="/uauth", service_name="ushauth"
    )
    headers = client._build_headers()
    assert headers["X-Forwarded-Proto"] == "https"
    assert headers["USHSPA-TOKEN"] == "sec-token"
    assert headers["X-USHSPA-TOKEN"] == "sec-token"
    assert headers["X-Service"] == "ushnotice"

