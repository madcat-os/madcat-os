"""MCPServer construction and the two transports.

HTTP is OAuth-only: the SDK's bearer middleware checks every ``/mcp`` request with
``auth.IntrospectionVerifier``, answers 401/403 with ``WWW-Authenticate … resource_metadata=``, and
serves RFC 9728 metadata at ``/.well-known/oauth-protected-resource/mcp``. This module adds the
origin-level discovery documents some clients probe instead, and ``/health``. stdio has no auth
and runs every call as the local user.
"""

from __future__ import annotations

import logging
import os
import signal
import time

import httpx
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from madcat_os import __version__
from madcat_os.auth import IntrospectionVerifier
from madcat_os.config import SERVER_NAME, Settings, settings
from madcat_os.instructions import INSTRUCTIONS
from madcat_os.tools import register_all
from madcat_os.users import enable_local_user

log = logging.getLogger("madcat_os")

SCOPES = ["mcp"]
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
AS_METADATA_TTL = 300.0
CORS = {"Access-Control-Allow-Origin": "*"}


def build_server(cfg: Settings | None = None, *, oauth: bool = False, transport=None) -> MCPServer:
    """The server with every tool registered. ``oauth`` wires madcat-web token checks (HTTP);
    without it there is no auth layer (stdio, CLI). ``transport`` is for tests (fake madcat-web)."""
    cfg = cfg or settings()
    kwargs: dict = {}
    if oauth:
        if not cfg.oauth_configured:
            raise ValueError(
                "HTTP needs OAuth: set MADCAT_PUBLIC_URL, MADCAT_OAUTH_ISSUER and "
                "MADCAT_OAUTH_INTROSPECT_SECRET (or [mcp] public_url, oauth_issuer, oauth_introspect_secret)"
            )
        kwargs["token_verifier"] = IntrospectionVerifier(cfg, transport=transport)
        kwargs["auth"] = AuthSettings(
            issuer_url=cfg.oauth_issuer,
            resource_server_url=cfg.resource_url,
            required_scopes=SCOPES,
            # The verifier checks the audience itself: madcat-web only sets aud when the client
            # sent a resource parameter.
            validate_token_resource=False,
        )
    mcp = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level=cfg.log_level.upper(),  # type: ignore[arg-type]
        **kwargs,
    )
    register_all(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True, "server": SERVER_NAME, "version": __version__})

    if oauth:
        _discovery_routes(mcp, cfg, transport)
    return mcp


def _discovery_routes(mcp: MCPServer, cfg: Settings, transport) -> None:
    issuer = (cfg.oauth_issuer or "").rstrip("/")
    resource_doc = {
        "resource": cfg.resource_url,
        "authorization_servers": [issuer],
        "scopes_supported": SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_name": "MADCAT MCP",
    }
    cache: dict = {}

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def protected_resource(_: Request) -> Response:
        return JSONResponse(resource_doc, headers=CORS)

    async def authorization_server(_: Request) -> Response:
        """The issuer's RFC 8414 metadata, for clients that look for it on this origin."""
        if cache.get("expires", 0) < time.time():
            try:
                async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
                    r = await client.get(f"{issuer}/.well-known/oauth-authorization-server")
                r.raise_for_status()
                cache.update(doc=r.json(), expires=time.time() + AS_METADATA_TTL)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("authorization server metadata unavailable: %s", e)
                return JSONResponse(
                    {"error": "authorization server metadata unavailable"}, status_code=502, headers=CORS
                )
        return JSONResponse(cache["doc"], headers=CORS)

    for path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp"):
        mcp.custom_route(path, methods=["GET"])(authorization_server)


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
    return mcp.streamable_http_app(
        streamable_http_path="/mcp", host=cfg.host, transport_security=transport_security(cfg)
    )


def serve_stdio(cfg: Settings | None = None) -> None:
    enable_local_user()
    mcp = build_server(cfg)
    # The stdio reader blocks in a thread, so asyncio's SIGINT cancellation would wait for stdin
    # forever; a client stopping the server with SIGINT expects it gone.
    signal.signal(signal.SIGINT, lambda *_: os._exit(130))
    mcp.run(transport="stdio")


def serve_http(cfg: Settings | None = None) -> None:
    import uvicorn

    cfg = cfg or settings()
    app = build_http_app(build_server(cfg, oauth=True), cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)
