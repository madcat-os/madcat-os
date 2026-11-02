"""EEMS memory tools: store, recall, get, forget, classify, list, link, graph.

Each runs against the calling user's own database. Names and arguments match the Rust madcat
tools; v2 enforces the rules Rust only described (see ``memory/store.py``).
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from madcat_os.memory.store import RELATIONS
from madcat_os.tools._run import run_memory


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="memory_store",
        description="Store a persistent memory under a subject. Embeds it for semantic recall. "
        "Storing the same content under the same subject and namespace returns the existing id.",
    )
    async def store(
        subject: Annotated[str, Field(description="Memory subject/category, e.g. user.preference")],
        content: Annotated[str, Field(description="Memory content to store")],
        classification: Annotated[
            str | None, Field(description="'standard' (default) or 'core' (permanent)")
        ] = None,
        confidence: Annotated[float | None, Field(description="Confidence 0.0-1.0 (default: 1.0)")] = None,
        namespace: Annotated[str | None, Field(description="Namespace (default: 'default')")] = None,
    ) -> CallToolResult:
        return await run_memory(
            lambda s: {"id": s.store(subject, content, classification, confidence, namespace)}
        )

    @mcp.tool(
        name="memory_recall",
        description="Recall memories: semantic (vector) and keyword (full-text) search fused by rank.",
    )
    async def recall(
        query: Annotated[str, Field(description="Query to search for")],
        limit: Annotated[int | None, Field(description="Max results (default: 10)")] = None,
        subject: Annotated[str | None, Field(description="Filter by subject")] = None,
        namespace: Annotated[str | None, Field(description="Filter by namespace")] = None,
    ) -> CallToolResult:
        return await run_memory(lambda s: s.recall(query, limit, subject, namespace))

    @mcp.tool(name="memory_get", description="Fetch full memory records by ID.")
    async def get(
        ids: Annotated[list[str], Field(description="Memory IDs to fetch (ULID strings)")],
    ) -> CallToolResult:
        return await run_memory(lambda s: s.get(ids))

    @mcp.tool(
        name="memory_forget",
        description="Delete a memory and its links. Core memories cannot be forgotten.",
    )
    async def forget(id: Annotated[str, Field(description="Memory ID")]) -> CallToolResult:
        return await run_memory(lambda s: {"deleted": s.forget(id)})

    @mcp.tool(
        name="memory_classify",
        description="Set a memory's classification. Promoting to 'core' is permanent: a core memory "
        "cannot be changed back or forgotten.",
    )
    async def classify(
        id: Annotated[str, Field(description="Memory ID")],
        classification: Annotated[
            str | None, Field(description="'standard' or 'core' (default: 'core')")
        ] = None,
    ) -> CallToolResult:
        return await run_memory(lambda s: s.classify(id, classification or "core") or {"classified": True})

    @mcp.tool(name="memory_list", description="List memory subjects with entry counts.")
    async def list_subjects(
        namespace: Annotated[str | None, Field(description="Filter by namespace")] = None,
    ) -> CallToolResult:
        return await run_memory(lambda s: s.list_subjects(namespace))

    @mcp.tool(
        name="memory_link",
        description=f"Link two memories. Relations: {', '.join(RELATIONS)}.",
    )
    async def link(
        from_id: Annotated[str, Field(description="Source memory ID")],
        to_id: Annotated[str, Field(description="Target memory ID")],
        relation: Annotated[str, Field(description=f"One of: {', '.join(RELATIONS)}")],
        weight: Annotated[float | None, Field(description="Edge weight 0.0-1.0 (default: 1.0)")] = None,
    ) -> CallToolResult:
        return await run_memory(lambda s: s.link(from_id, to_id, relation, weight) or {"linked": True})

    @mcp.tool(
        name="memory_graph",
        description="Memories linked to one memory, up to N hops, with their links.",
    )
    async def graph(
        id: Annotated[str, Field(description="Memory ID to explore from")],
        depth: Annotated[int | None, Field(description="Max traversal depth (default: 2)")] = None,
    ) -> CallToolResult:
        return await run_memory(lambda s: s.graph(id, 2 if depth is None else depth))
