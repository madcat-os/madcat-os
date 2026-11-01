"""System tools: identity and liveness."""

from __future__ import annotations

import platform
import socket
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from madcat_os import __version__
from madcat_os.config import SERVER_NAME


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def ping() -> str:
        """Liveness check. Returns 'pong'."""
        return "pong"

    @mcp.tool()
    def server_info() -> dict:
        """Server identity: name, version, host, platform and current UTC time."""
        return {
            "server": {"name": SERVER_NAME, "version": __version__},
            "host": {
                "hostname": socket.gethostname(),
                "platform": platform.system().lower(),
                "arch": platform.machine(),
                "python": platform.python_version(),
            },
            "datetime": {"iso": datetime.now(UTC).isoformat()},
        }
