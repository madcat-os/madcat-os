"""Tool registry.

Each module exposes ``register(mcp: MCPServer) -> None``. Tools that touch user data resolve the
caller through ``tools._run`` and never take identity from their arguments.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from . import memory, system

MODULES: tuple[Callable[[MCPServer], None], ...] = (system.register, memory.register)


def register_all(mcp: MCPServer) -> None:
    for register in MODULES:
        register(mcp)
