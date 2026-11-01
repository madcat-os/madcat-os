"""Tool registry.

Each module exposes ``register(mcp: MCPServer) -> None`` and decorates its handlers with
``@mcp.tool()``. Add a module here and it ships in every transport and in ``madcat mcp list``.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from . import system

MODULES: tuple[Callable[[MCPServer], None], ...] = (system.register,)


def register_all(mcp: MCPServer) -> None:
    for register in MODULES:
        register(mcp)
