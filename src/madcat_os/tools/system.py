"""System tools: who the caller is."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from madcat_os.tools._run import run


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="whoami",
        description="The account this call runs as: user id, email, name and its database name.",
    )
    async def whoami() -> CallToolResult:
        return await run(lambda user: user.public())
