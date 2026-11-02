"""System tools: liveness, server identity, and who the caller is."""

from __future__ import annotations

import platform
import socket
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from madcat_os import __version__
from madcat_os.config import SERVER_NAME
from madcat_os.tools._run import ok, run


def register(mcp: MCPServer) -> None:
    @mcp.tool(name="ping", description="Liveness check. Returns 'pong'.")
    def ping() -> CallToolResult:
        return ok("pong")

    @mcp.tool(
        name="server_info",
        description="Server identity: name, version, host, platform and current UTC time.",
    )
    def server_info() -> CallToolResult:
        return ok(
            {
                "server": {"name": SERVER_NAME, "version": __version__},
                "host": {
                    "hostname": socket.gethostname(),
                    "platform": platform.system().lower(),
                    "arch": platform.machine(),
                    "python": platform.python_version(),
                },
                "datetime": {"iso": datetime.now(UTC).isoformat()},
            }
        )

    @mcp.tool(
        name="whoami",
        description="The account this call runs as: user id, email, name and its database name.",
    )
    async def whoami() -> CallToolResult:
        return await run(lambda user: user.public())
