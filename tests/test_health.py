from httpx import AsyncClient


async def test_healthz_at_root_and_api_prefix(client: AsyncClient) -> None:
    for path in ("/healthz", "/api/v1/healthz"):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_readyz_checks_database(client: AsyncClient) -> None:
    for path in ("/readyz", "/api/v1/readyz"):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}


async def test_request_id_is_generated_and_echoed(client: AsyncClient) -> None:
    generated = await client.get("/healthz")
    assert len(generated.headers["X-Request-ID"]) == 32

    echoed = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"

    sanitized = await client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert sanitized.headers["X-Request-ID"] != "bad id\twith spaces"


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "X-Request-ID" in response.headers
