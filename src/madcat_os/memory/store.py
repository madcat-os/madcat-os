"""EEMS memory store over one user's database.

The schema and the hybrid recall come from the Rust madcat (``db/migrations/001_init.sql``):
pgvector cosine (``<=>``) and tsvector ``ts_rank`` ranks fused with reciprocal rank fusion in SQL.
v2 enforces what Rust only described: ``core`` memories are permanent, forgetting a memory removes
its edges, and classifications, relations, confidences and weights are validated.

The store is synchronous (psycopg pool); async tool handlers call it from a worker thread.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from ulid import ULID

from madcat_os.memory.embed import Embedder, EmbeddingError

CLASSIFICATIONS = ("standard", "core")
RELATIONS = ("related_to", "depends_on", "contradicts", "extends", "supersedes")

# real columns go through float8 so the value is the stored f32 widened to f64.
MEMORY_COLS = (
    "id, namespace, subject, content, classification, confidence::float8 AS confidence, "
    "metadata, created_at, updated_at"
)
MEMORY_FIELDS = (
    "id",
    "namespace",
    "subject",
    "content",
    "classification",
    "confidence",
    "metadata",
    "created_at",
    "updated_at",
)
EDGE_COLS = "from_id, to_id, relation, weight::float8 AS weight, created_at"

RECALL_SQL = """
WITH params AS (
    SELECT %(qvec)s::vector AS qvec,
           %(qtext)s::text  AS qtext,
           %(fetch_n)s::int AS fetch_n,
           %(ns)s::text     AS ns,
           %(subj)s::text   AS subj
),
filtered AS (
    SELECT m.* FROM memories m, params p
    WHERE (p.ns   IS NULL OR m.namespace = p.ns)
      AND (p.subj IS NULL OR m.subject   = p.subj)
),
vec_ranked AS (
    SELECT f.id,
           row_number() OVER (ORDER BY f.embedding <=> p.qvec ASC) AS rnk
    FROM filtered f, params p
    WHERE f.embedding IS NOT NULL
    ORDER BY f.embedding <=> p.qvec ASC
    LIMIT (SELECT fetch_n FROM params)
),
fts_ranked AS (
    SELECT f.id,
           row_number() OVER (
               ORDER BY ts_rank(f.content_tsv, websearch_to_tsquery('english', p.qtext)) DESC
           ) AS rnk
    FROM filtered f, params p
    WHERE f.content_tsv @@ websearch_to_tsquery('english', p.qtext)
    ORDER BY ts_rank(f.content_tsv, websearch_to_tsquery('english', p.qtext)) DESC
    LIMIT (SELECT fetch_n FROM params)
),
fused AS (
    SELECT id, SUM(score)::double precision AS score FROM (
        SELECT id, (1.0 / (60.0 + rnk))::double precision AS score FROM vec_ranked
        UNION ALL
        SELECT id, (1.0 / (60.0 + rnk))::double precision AS score FROM fts_ranked
    ) u
    GROUP BY id
)
SELECT m.id, m.namespace, m.subject, m.content, m.classification,
       m.confidence::float8 AS confidence, m.metadata, m.created_at, m.updated_at,
       fused.score
FROM fused
JOIN memories m ON m.id = fused.id
ORDER BY fused.score DESC, m.id
LIMIT %(limit)s
"""


class StoreError(Exception):
    """A refused or failed store operation; the message is shown to the caller."""


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id() -> str:
    return str(ULID()).lower()


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _memory(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in MEMORY_FIELDS}


def _unit(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise StoreError(f"{name} must be between 0 and 1")


def traverse(
    root: str,
    depth: int,
    get_memory: Callable[[str], dict | None],
    get_edges: Callable[[str], list[dict]],
) -> list[dict]:
    """Graph neighbourhood of ``root`` up to ``depth`` hops. A stack, so nodes come out
    depth-first and a node may be reported deeper than its shortest path; every incident edge of
    each node is listed."""
    visited: dict[str, int] = {}
    stack = [(root, 0)]
    nodes = []
    while stack:
        current, d = stack.pop()
        if d > depth or current in visited:
            continue
        visited[current] = d
        memory = get_memory(current)
        if memory is None:
            continue
        edges = get_edges(current)
        for e in edges:
            neighbor = e["to_id"] if e["from_id"] == current else e["from_id"]
            if neighbor not in visited:
                stack.append((neighbor, d + 1))
        nodes.append({"memory": memory, "edges": edges, "depth": d})
    return nodes


class MemoryStore:
    def __init__(self, pool: ConnectionPool, embedder: Callable[[], Embedder]) -> None:
        self.pool = pool
        self._embedder = embedder

    def _embed(self, text: str) -> tuple[str, str]:
        """(vector literal, model id) for ``text``."""
        embedder = self._embedder()
        vectors = embedder.embed([text])
        if not vectors:
            raise EmbeddingError("no embedding in response")
        return vector_literal(vectors[0]), embedder.model_id

    def _one(self, conn: psycopg.Connection, memory_id: str) -> dict | None:
        row = conn.execute(f"SELECT {MEMORY_COLS} FROM memories WHERE id = %s", (memory_id,)).fetchone()
        return _memory(row) if row else None

    # ── Store ───────────────────────────────────────────────

    def store(
        self,
        subject: str,
        content: str,
        classification: str | None = None,
        confidence: float | None = None,
        namespace: str | None = None,
    ) -> str:
        """Store a memory and return its id. The same namespace, subject and content returns the
        existing id without embedding again."""
        classification = classification or "standard"
        if classification not in CLASSIFICATIONS:
            raise StoreError("classification must be 'standard' or 'core'")
        confidence = 1.0 if confidence is None else confidence
        _unit("confidence", confidence)
        ns = namespace or "default"
        digest = content_hash(content)
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE namespace = %s AND subject = %s AND content_hash = %s LIMIT 1",
                (ns, subject, digest),
            ).fetchone()
            if row:
                return row["id"]
            vector, model = self._embed(content)
            memory_id = new_id()
            now = now_ms()
            conn.execute(
                "INSERT INTO memories (id, namespace, subject, content, content_hash, classification, "
                "confidence, embedding, embedding_model, metadata, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s::real, %s::vector, %s, %s, %s, %s)",
                (memory_id, ns, subject, content, digest, classification, confidence, vector, model,
                 Jsonb({}), now, now),
            )  # fmt: skip
            return memory_id

    # ── Read ────────────────────────────────────────────────

    def recall(
        self, query: str, limit: int | None = None, subject: str | None = None, namespace: str | None = None
    ) -> list[dict]:
        """Hybrid recall: pgvector cosine + tsvector ts_rank, RRF-fused in SQL."""
        if not query.strip():
            raise StoreError("query must not be empty")
        limit = 10 if limit is None else limit
        if limit < 1:
            raise StoreError("limit must be at least 1")
        vector, _ = self._embed(query)
        params = {
            "qvec": vector,
            "qtext": query,
            "fetch_n": max(limit * 3, 30),
            "ns": namespace,
            "subj": subject,
            "limit": limit,
        }
        with self.pool.connection() as conn:
            rows = conn.execute(RECALL_SQL, params).fetchall()
        return [{"memory": _memory(r), "score": r["score"]} for r in rows]

    def get(self, ids: list[str]) -> list[dict]:
        """Memories by id, in the order asked for; unknown ids are skipped."""
        if not ids:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {MEMORY_COLS} FROM memories WHERE id = ANY(%s)", (ids,)).fetchall()
        found = {r["id"]: _memory(r) for r in rows}
        return [found[i] for i in dict.fromkeys(ids) if i in found]

    # ── Change ──────────────────────────────────────────────

    def forget(self, memory_id: str) -> bool:
        """Delete a memory and its edges. ``core`` memories cannot be forgotten."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT classification FROM memories WHERE id = %s FOR UPDATE", (memory_id,)
            ).fetchone()
            if row is None:
                return False
            if row["classification"] == "core":
                raise StoreError(f"memory {memory_id} is core and cannot be forgotten")
            conn.execute("DELETE FROM edges WHERE from_id = %s OR to_id = %s", (memory_id, memory_id))
            conn.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
            return True

    def classify(self, memory_id: str, classification: str) -> None:
        """Set a memory's classification. Promotion to ``core`` is permanent."""
        if classification not in CLASSIFICATIONS:
            raise StoreError("classification must be 'standard' or 'core'")
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT classification FROM memories WHERE id = %s FOR UPDATE", (memory_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"not found: {memory_id}")
            if row["classification"] == classification:
                return
            if row["classification"] == "core":
                raise StoreError(f"memory {memory_id} is core and cannot change")
            conn.execute(
                "UPDATE memories SET classification = %s, updated_at = %s WHERE id = %s",
                (classification, now_ms(), memory_id),
            )

    def link(self, from_id: str, to_id: str, relation: str, weight: float | None = None) -> None:
        if relation not in RELATIONS:
            raise StoreError(f"relation must be one of: {', '.join(RELATIONS)}")
        if from_id == to_id:
            raise StoreError("a memory cannot link to itself")
        weight = 1.0 if weight is None else weight
        _unit("weight", weight)
        with self.pool.connection() as conn, conn.transaction():
            found = {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM memories WHERE id = ANY(%s) FOR SHARE", ([from_id, to_id],)
                ).fetchall()
            }
            for memory_id in (from_id, to_id):
                if memory_id not in found:
                    raise StoreError(f"not found: {memory_id}")
            conn.execute(
                "INSERT INTO edges (from_id, to_id, relation, weight, created_at) "
                "VALUES (%s, %s, %s, %s::real, %s) ON CONFLICT (from_id, to_id, relation) "
                "DO UPDATE SET weight = EXCLUDED.weight, created_at = EXCLUDED.created_at",
                (from_id, to_id, relation, weight, now_ms()),
            )

    def graph(self, memory_id: str, depth: int) -> list[dict]:
        if depth < 0:
            raise StoreError("depth must be 0 or more")
        with self.pool.connection() as conn:

            def get_edges(i: str) -> list[dict]:
                return conn.execute(
                    f"SELECT {EDGE_COLS} FROM edges WHERE from_id = %s OR to_id = %s "
                    "ORDER BY created_at, from_id, to_id, relation",
                    (i, i),
                ).fetchall()

            return traverse(memory_id, depth, lambda i: self._one(conn, i), get_edges)
