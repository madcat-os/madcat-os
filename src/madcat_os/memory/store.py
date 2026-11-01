"""EEMS memory store on Postgres + pgvector.

A port of the Rust madcat ``src/madcat_memory/pg.rs`` that reads and writes the same tables, so
both servers can share one database. Keep the SQL in step with it. Hybrid recall fuses pgvector
cosine (``<=>``) and tsvector ``ts_rank`` with reciprocal rank fusion in SQL.

The store is synchronous (a psycopg connection pool is not tied to an event loop); async tool
handlers call it from a worker thread.
"""

from __future__ import annotations

import atexit
import hashlib
import threading
import time
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from ulid import ULID

from madcat_os import config
from madcat_os.memory.embed import Embedder, EmbeddingError, create_embedder

MIGRATION = files("madcat_os.memory").joinpath("pg_001_init.sql").read_text()

# Columns of a Memory. real columns go through float8 so the value is the f32 widened to f64,
# the number Rust prints (0.8999999761581543, not 0.9).
MEMORY_COLS = (
    "id, namespace, subject, content, classification, confidence::float8 AS confidence, "
    "metadata, created_at, updated_at"
)

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
           row_number() OVER (
               ORDER BY f.embedding <=> p.qvec ASC
           ) AS rnk
    FROM filtered f, params p
    WHERE f.embedding IS NOT NULL
    ORDER BY f.embedding <=> p.qvec ASC
    LIMIT (SELECT fetch_n FROM params)
),
fts_ranked AS (
    SELECT f.id,
           row_number() OVER (
               ORDER BY ts_rank(
                   f.content_tsv,
                   websearch_to_tsquery('english', p.qtext)
               ) DESC
           ) AS rnk
    FROM filtered f, params p
    WHERE f.content_tsv @@ websearch_to_tsquery('english', p.qtext)
    ORDER BY ts_rank(
                 f.content_tsv,
                 websearch_to_tsquery('english', p.qtext)
             ) DESC
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
ORDER BY fused.score DESC
LIMIT %(limit)s
"""


class StoreError(Exception):
    """Display text matches the Rust MemoryError (``postgres: …``, ``not found: <id>``)."""


def _pg(e: psycopg.Error) -> StoreError:
    return StoreError(f"postgres: {e}")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id() -> str:
    return str(ULID()).lower()


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


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


def _memory(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in MEMORY_FIELDS}


def traverse(
    root: str,
    depth: int,
    get_memory: Callable[[str], dict | None],
    get_edges: Callable[[str], list[dict]],
) -> list[dict]:
    """Graph neighbourhood of ``root`` up to ``depth`` hops, in the Rust store's exact order: a
    stack (last pushed, first visited), so nodes come out depth-first, a node may be reported at a
    deeper depth than its shortest path, and every incident edge is listed."""
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


class PgStore:
    def __init__(self, dsn: str, embedder: Embedder) -> None:
        self.embedder = embedder
        self.pool = ConnectionPool(
            dsn, min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=False, name="eems"
        )
        try:
            self.pool.open(wait=True, timeout=10)
            self._migrate()
        except psycopg.Error as e:
            self.pool.close()
            raise _pg(e) from e
        except Exception:
            self.pool.close()
            raise

    def close(self) -> None:
        self.pool.close()

    def _ensure_vector(self, conn: psycopg.Connection) -> None:
        """pgvector is not a trusted extension: an installed one is used as is, a missing one is
        created only when the role may, else the error names the SQL a superuser has to run."""
        if conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
            return
        try:
            with conn.transaction():
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except psycopg.errors.InsufficientPrivilege:
            row = conn.execute("SELECT current_database() AS db").fetchone()
            db = row["db"] if row else "this database"
            raise StoreError(
                f"pgvector is not installed in database {db} and this role may not install it; "
                f"as a superuser run `CREATE EXTENSION vector;` in {db} (or once in template1, "
                "or set [database].user_template to a database that has it)"
            ) from None

    def _migrate(self) -> None:
        with self.pool.connection() as conn:
            self._ensure_vector(conn)
            # No parameters: psycopg sends the multi-statement batch as one simple query.
            conn.execute(MIGRATION)

    # ── Store ───────────────────────────────────────────────

    def store(
        self,
        subject: str,
        content: str,
        classification: str | None = None,
        confidence: float | None = None,
        namespace: str | None = None,
        metadata: Any = None,
    ) -> str:
        """Store a memory and return its id. Same namespace, subject and content returns the
        existing id without embedding again."""
        ns = namespace or "default"
        digest = content_hash(content)
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    "SELECT id FROM memories WHERE namespace = %s AND subject = %s "
                    "AND content_hash = %s LIMIT 1",
                    (ns, subject, digest),
                ).fetchone()
                if row:
                    return row["id"]
                vector = self._embed_one(content)
                memory_id = new_id()
                now = now_ms()
                conn.execute(
                    "INSERT INTO memories (id, namespace, subject, content, content_hash, "
                    "classification, confidence, embedding, embedding_model, metadata, "
                    "created_at, updated_at) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s::real, %s::vector, %s, %s, %s, %s)",
                    (
                        memory_id,
                        ns,
                        subject,
                        content,
                        digest,
                        classification or "standard",
                        1.0 if confidence is None else confidence,
                        vector_literal(vector),
                        self.embedder.model_id,
                        Jsonb({} if metadata is None else metadata),
                        now,
                        now,
                    ),
                )
                return memory_id
        except psycopg.Error as e:
            raise _pg(e) from e

    def _embed_one(self, text: str) -> list[float]:
        vectors = self.embedder.embed([text])
        if not vectors:
            raise EmbeddingError("no embedding in response")
        return vectors[0]

    # ── Recall ──────────────────────────────────────────────

    def recall(
        self,
        query: str,
        limit: int | None = None,
        subject: str | None = None,
        namespace: str | None = None,
    ) -> list[dict]:
        """Hybrid recall: pgvector cosine + tsvector ts_rank, RRF-fused in SQL."""
        limit = 10 if limit is None else limit
        vector = self._embed_one(query)
        params = {
            "qvec": vector_literal(vector),
            "qtext": query,
            "fetch_n": max(limit * 3, 30),
            "ns": namespace,
            "subj": subject,
            "limit": limit,
        }
        try:
            with self.pool.connection() as conn:
                rows = conn.execute(RECALL_SQL, params).fetchall()
        except psycopg.Error as e:
            raise _pg(e) from e
        return [{"memory": _memory(r), "score": r["score"]} for r in rows]

    # ── Get / forget / classify ─────────────────────────────

    def get(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        try:
            with self.pool.connection() as conn:
                rows = conn.execute(
                    f"SELECT {MEMORY_COLS} FROM memories WHERE id = ANY(%s)", (ids,)
                ).fetchall()
        except psycopg.Error as e:
            raise _pg(e) from e
        return [_memory(r) for r in rows]

    def forget(self, memory_id: str) -> bool:
        try:
            with self.pool.connection() as conn:
                cur = conn.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
                return cur.rowcount > 0
        except psycopg.Error as e:
            raise _pg(e) from e

    def classify(self, memory_id: str, classification: str) -> None:
        try:
            with self.pool.connection() as conn:
                cur = conn.execute(
                    "UPDATE memories SET classification = %s, updated_at = %s WHERE id = %s",
                    (classification, now_ms(), memory_id),
                )
        except psycopg.Error as e:
            raise _pg(e) from e
        if cur.rowcount == 0:
            raise StoreError(f"not found: {memory_id}")

    # ── List ────────────────────────────────────────────────

    def list_subjects(self, namespace: str | None = None) -> list[dict]:
        try:
            with self.pool.connection() as conn:
                rows = conn.execute(
                    "SELECT subject, COUNT(*)::bigint AS cnt FROM memories "
                    "WHERE (%s::text IS NULL OR namespace = %s) GROUP BY subject ORDER BY cnt DESC",
                    (namespace, namespace),
                ).fetchall()
        except psycopg.Error as e:
            raise _pg(e) from e
        return [{"subject": r["subject"], "count": r["cnt"]} for r in rows]

    # ── Graph ───────────────────────────────────────────────

    def link(self, from_id: str, to_id: str, relation: str, weight: float | None = None) -> None:
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO edges (from_id, to_id, relation, weight, created_at) "
                    "VALUES (%s, %s, %s, %s::real, %s) ON CONFLICT (from_id, to_id, relation) "
                    "DO UPDATE SET weight = EXCLUDED.weight, created_at = EXCLUDED.created_at",
                    (from_id, to_id, relation, 1.0 if weight is None else weight, now_ms()),
                )
        except psycopg.Error as e:
            raise _pg(e) from e

    def edges_for(self, memory_id: str) -> list[dict]:
        try:
            with self.pool.connection() as conn:
                return conn.execute(
                    "SELECT from_id, to_id, relation, weight::float8 AS weight, created_at "
                    "FROM edges WHERE from_id = %s OR to_id = %s",
                    (memory_id, memory_id),
                ).fetchall()
        except psycopg.Error as e:
            raise _pg(e) from e

    def graph(self, memory_id: str, depth: int) -> list[dict]:
        def get_memory(i: str) -> dict | None:
            found = self.get([i])
            return found[0] if found else None

        return traverse(memory_id, depth, get_memory, self.edges_for)


_stores: dict[str, PgStore] = {}
_lock = threading.Lock()


def default_store() -> PgStore:
    """The process-wide store on ``EEMS_PG_DSN`` / ``[database].dsn``, opened on first use."""
    dsn = config.eems_pg_dsn()
    if not dsn:
        raise StoreError(
            "No Postgres DSN — set [database].dsn in ~/.config/madcat/config.toml or EEMS_PG_DSN"
        )
    with _lock:
        if (store := _stores.get(dsn)) is None:
            try:
                embedder = create_embedder()
            except EmbeddingError as e:
                raise StoreError(f"embedding provider: {e}") from e
            try:
                store = PgStore(dsn, embedder)
            except Exception as e:
                raise StoreError(f"EEMS store: {e}") from e
            # Close before interpreter teardown; the pool's __del__ cannot log at that point.
            atexit.register(store.close)
            _stores[dsn] = store
        return store
