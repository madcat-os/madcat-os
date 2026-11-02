"""Shared plumbing for tool handlers: resolve the caller's store, run off the event loop, render.

Every tool returns one text block of pretty-printed JSON with sorted keys; a failure is
``{"error": "<message>"}`` with ``isError`` set. Database errors are reported by the server's own
message only, never with a connection string.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import anyio
import psycopg
from mcp.types import CallToolResult, TextContent

from madcat_os.db.migrate import MigrationError
from madcat_os.db.pool import DatabaseError, pools
from madcat_os.memory.embed import Embedder, EmbeddingError, create_embedder
from madcat_os.memory.store import MemoryStore, StoreError
from madcat_os.users import User, UserError, current_user

log = logging.getLogger("madcat_os.tools")

FAILURES = (UserError, StoreError, EmbeddingError, DatabaseError, MigrationError)

_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def embedder() -> Embedder:
    """The process-wide embedder, created on the first call that needs one."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = create_embedder()
        return _embedder


def render(value: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def ok(value: Any) -> CallToolResult:
    return CallToolResult(content=[render(value)])


def error(message: str) -> CallToolResult:
    return CallToolResult(content=[render({"error": message})], is_error=True)


def memory_store(user: User) -> MemoryStore:
    db_name, db_url = user.database()
    return MemoryStore(pools().get(db_name, db_url), embedder)


async def run(f: Callable[[User], Any]) -> CallToolResult:
    """Resolve the calling user, run ``f(user)`` on a worker thread, render result or error."""
    try:
        user = current_user()
        value = await anyio.to_thread.run_sync(lambda: f(user))
    except FAILURES as e:
        return error(str(e))
    except psycopg.Error as e:
        log.warning("database error: %s", type(e).__name__)
        return error(f"postgres: {e.diag.message_primary or type(e).__name__}")
    return ok(value)


async def run_memory(f: Callable[[MemoryStore], Any]) -> CallToolResult:
    return await run(lambda user: f(memory_store(user)))
