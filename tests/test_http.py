"""The HTTP server in-process against a fake madcat-web: discovery, 401/403, and identity in tools."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from madcat_os.server import build_http_app, build_server
from tests.fake_web import ISSUER, PUBLIC, FakeWeb, oauth_settings

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


@asynccontextmanager
async def client(web: FakeWeb | None = None):
    """Lifespan + client in one task: anyio cancel scopes must exit where they were entered."""
    web = web or FakeWeb()
    cfg = oauth_settings()
    app = build_http_app(build_server(cfg, oauth=True, transport=web.transport), cfg)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4097") as c:
            yield c


def messages(r: httpx.Response) -> list[dict]:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        return [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    return [r.json()]


async def call_tool(c: httpx.AsyncClient, token: str, name: str, args: dict | None = None) -> dict:
    auth = {**HEADERS, "Authorization": f"Bearer {token}"}
    r = await c.post("/mcp", json=INIT, headers=auth)
    assert r.status_code == 200, r.text
    session = {**auth, "mcp-session-id": r.headers["mcp-session-id"]}
    await c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=session)
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    }
    r = await c.post("/mcp", json=body, headers=session)
    assert r.status_code == 200, r.text
    assert "hunter2" not in r.text
    return [m for m in messages(r) if m.get("id") == 2][0]["result"]


async def test_health_and_discovery_are_open():
    async with client() as c:
        assert (await c.get("/health")).json()["ok"] is True
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            doc = (await c.get(path)).json()
            assert doc["resource"].rstrip("/") == f"{PUBLIC}/mcp", path
            assert [s.rstrip("/") for s in doc["authorization_servers"]] == [ISSUER], path
        for path in (
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-authorization-server/mcp",
        ):
            assert (await c.get(path)).json()["issuer"] == ISSUER


@pytest.mark.parametrize("token", [None, "nope", "wrong-aud"])
async def test_mcp_without_a_valid_token_is_401(token):
    headers = {**HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    async with client() as c:
        r = await c.post("/mcp", json=INIT, headers=headers)
    assert r.status_code == 401
    assert (
        f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"'
        in r.headers["www-authenticate"]
    )


async def test_token_without_mcp_scope_is_403():
    async with client() as c:
        r = await c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer no-scope"})
    assert r.status_code == 403
    assert "insufficient_scope" in r.headers["www-authenticate"]


async def test_tools_run_as_the_token_user():
    async with client() as c:
        ada = await call_tool(c, "good", "whoami")
        bob = await call_tool(c, "other-user", "whoami")
    assert json.loads(ada["content"][0]["text"]) == {
        "db_name": "swift-ada-01",
        "email": "ada@example.com",
        "id": "u-ada",
        "local": False,
        "name": "Ada",
    }
    assert json.loads(bob["content"][0]["text"])["db_name"] == "bold-bob-02"


async def test_account_without_database_gets_a_tool_error():
    async with client() as c:
        result = await call_tool(c, "no-db", "memory_get", {"ids": []})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"]) == {"error": "no database provisioned for this account"}
