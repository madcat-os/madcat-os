"""MCPServer construction and the two transports.

``build_server()`` is the single place the tool registry meets the MCP server; the CLI,
the stdio entrypoint and the HTTP app all go through it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from madcat_os import __version__
from madcat_os.config import SERVER_NAME, Settings, settings
from madcat_os.tools import register_all

log = logging.getLogger("madcat_os")

INSTRUCTIONS = "MADCAT MCP server. Call server_info at session start for identity."
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def build_server(cfg: Settings | None = None) -> MCPServer:
    cfg = cfg or settings()
    mcp = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level=cfg.log_level.upper(),  # type: ignore[arg-type]
    )
    register_all(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True, "server": SERVER_NAME, "version": __version__})

    return mcp


class BearerAuth(BaseHTTPMiddleware):
    """Static-token guard for the HTTP transport. Health stays open."""

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        if request.headers.get("authorization", "") != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def transport_security(cfg: Settings) -> TransportSecuritySettings:
    if cfg.allowed_hosts:
        return TransportSecuritySettings(allowed_hosts=cfg.allowed_hosts)
    if cfg.host in LOOPBACK:
        return TransportSecuritySettings(
            allowed_hosts=[f"{cfg.host}:{cfg.port}", f"{cfg.host}:*", "localhost:*"]
        )
    # Public bind (0.0.0.0, behind a proxy): the proxy owns the Host header.
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def build_http_app(mcp: MCPServer, cfg: Settings | None = None) -> Starlette:
    cfg = cfg or settings()
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=cfg.host,
        transport_security=transport_security(cfg),
    )
    if cfg.token:
        app.add_middleware(BearerAuth, token=cfg.token)
    else:
        log.warning("HTTP transport without a token: anyone who can reach the port can call tools")
    return app


def serve_stdio(mcp: MCPServer) -> None:
    mcp.run(transport="stdio")


def serve_http(mcp: MCPServer, cfg: Settings | None = None) -> None:
    import uvicorn

    cfg = cfg or settings()
    uvicorn.run(build_http_app(mcp, cfg), host=cfg.host, port=cfg.port, log_level=cfg.log_level)
