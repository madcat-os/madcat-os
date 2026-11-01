from contextlib import asynccontextmanager

import httpx

from madcat_os.config import Settings
from madcat_os.server import build_http_app, build_server

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
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def test_tools_registered():
    tools = await build_server(Settings()).list_tools()
    assert {t.name for t in tools} >= {"ping", "server_info"}


async def test_call_tool_direct():
    result = await build_server(Settings()).call_tool("ping", {})
    assert result.content[0].text == "pong"


@asynccontextmanager
async def http_client():
    """Lifespan + client in one task: anyio cancel scopes must exit where they were entered."""
    cfg = Settings(token="t", port=4097)
    app = build_http_app(build_server(cfg), cfg)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4097") as c:
            yield c


async def test_health_open():
    async with http_client() as client:
        r = await client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


async def test_mcp_requires_token():
    async with http_client() as client:
        r = await client.post("/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401


async def test_mcp_initialize():
    async with http_client() as client:
        r = await client.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer t"})
    assert r.status_code == 200, r.text
    assert "mcp-session-id" in r.headers
    assert "madcat" in r.text
