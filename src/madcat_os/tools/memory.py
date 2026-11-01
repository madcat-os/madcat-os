"""EEMS memory tools: store, recall, get, forget, classify, list, link, graph.

Names, arguments, defaults and descriptions match the Rust madcat ``src/tools/memory.rs``. Each
tool returns one text block of pretty-printed JSON with sorted keys, the exact text the Rust server
sends (serde_json without ``preserve_order`` sorts object keys); a failure is ``{"error": …}`` with
``isError``, as in Rust.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from madcat_os.memory.embed import EmbeddingError
from madcat_os.memory.store import PgStore, StoreError, default_store


def render(value: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


async def run(f: Callable[[PgStore], Any]) -> CallToolResult:
    """Run a blocking store call on a worker thread and render its result or its error."""
    try:
        value = await anyio.to_thread.run_sync(lambda: f(default_store()))
    except (StoreError, EmbeddingError) as e:
        return CallToolResult(content=[render({"error": str(e)})], is_error=True)
    return CallToolResult(content=[render(value)])


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="memory_store",
        description="Store persistent memories with subject and content. Embeds for semantic recall.",
    )
    async def store(
        subject: Annotated[str, Field(description="Memory subject/category")],
        content: Annotated[str, Field(description="Memory content to store")],
        classification: Annotated[
            str | None, Field(description="'standard' (default) or 'core' (immutable)")
        ] = None,
        confidence: Annotated[float | None, Field(description="Confidence 0.0-1.0 (default: 1.0)")] = None,
        namespace: Annotated[str | None, Field(description="Namespace (default: 'default')")] = None,
    ) -> CallToolResult:
        return await run(lambda s: {"id": s.store(subject, content, classification, confidence, namespace)})

    @mcp.tool(
        name="memory_recall",
        description="Recall memories by semantic similarity. "
        "Hybrid: FTS5 keyword + vector cosine fused via RRF.",
    )
    async def recall(
        query: Annotated[str, Field(description="Query to search for")],
        limit: Annotated[int | None, Field(description="Max results (default: 10)")] = None,
        subject: Annotated[str | None, Field(description="Filter by subject")] = None,
        namespace: Annotated[str | None, Field(description="Filter by namespace")] = None,
    ) -> CallToolResult:
        return await run(lambda s: s.recall(query, limit, subject, namespace))

    @mcp.tool(name="memory_get", description="Fetch full memory records by ID (no truncation).")
    async def get(
        ids: Annotated[list[str], Field(description="Memory IDs to fetch (ULID strings)")],
    ) -> CallToolResult:
        return await run(lambda s: s.get(ids))

    @mcp.tool(name="memory_forget", description="Delete a specific memory entry by its ID.")
    async def forget(id: Annotated[str, Field(description="Memory ID")]) -> CallToolResult:
        return await run(lambda s: {"deleted": s.forget(id)})

    @mcp.tool(
        name="memory_classify",
        description="Promote a memory to core classification. Immutable and irreversible.",
    )
    async def classify(
        id: Annotated[str, Field(description="Memory ID")],
        classification: Annotated[
            str | None, Field(description="Classification: 'standard' or 'core' (default: 'core')")
        ] = None,
    ) -> CallToolResult:
        return await run(lambda s: s.classify(id, classification or "core") or {"classified": True})

    @mcp.tool(
        name="memory_list",
        description="List all memory subjects with entry counts. Overview of stored knowledge.",
    )
    async def list_subjects(
        namespace: Annotated[str | None, Field(description="Filter by namespace")] = None,
    ) -> CallToolResult:
        return await run(lambda s: s.list_subjects(namespace))

    @mcp.tool(
        name="memory_link",
        description="Create a relationship edge between two memories. "
        "Relations: related_to, depends_on, contradicts, extends, supersedes.",
    )
    async def link(
        from_id: Annotated[str, Field(description="Source memory ID")],
        to_id: Annotated[str, Field(description="Target memory ID")],
        relation: Annotated[str, Field(description="Relationship type")],
        weight: Annotated[float | None, Field(description="Edge weight 0.0-1.0 (default: 1.0)")] = None,
    ) -> CallToolResult:
        return await run(lambda s: s.link(from_id, to_id, relation, weight) or {"linked": True})

    @mcp.tool(
        name="memory_graph",
        description="Explore memory graph neighborhood. Shows connected memories up to N hops.",
    )
    async def graph(
        id: Annotated[str, Field(description="Memory ID to explore from")],
        depth: Annotated[int | None, Field(description="Max traversal depth (default: 2)", ge=0)] = None,
    ) -> CallToolResult:
        return await run(lambda s: s.graph(id, 2 if depth is None else depth))
