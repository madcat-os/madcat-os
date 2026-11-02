"""Live Postgres + pgvector tests. Skipped unless MADCAT_TEST_DB_URL is set.

MADCAT_TEST_DB_URL must point at a throwaway database with pgvector installed: every test starts
by dropping its tables. Embeddings come from a deterministic stub, so no model endpoint is needed.
"""

import os

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from madcat_os.db.migrate import migrate, migrations
from madcat_os.memory.store import MemoryStore, StoreError

DB_URL = os.environ.get("MADCAT_TEST_DB_URL", "").strip()
pytestmark = pytest.mark.skipif(not DB_URL, reason="MADCAT_TEST_DB_URL not set")

U64 = (1 << 64) - 1
TABLES = "memories, edges, chunks_code, chunks_docs, schema_migrations"


class StubEmbed:
    """FNV-1a seed, xorshift, 1024 values in [-1, 1] (the Rust test's stub)."""

    model_id = "stub-1024"

    def embed(self, texts):
        out = []
        for text in texts:
            seed = 1469598103934665603
            for b in text.encode():
                seed = ((seed ^ b) * 1099511628211) & U64
            vector = []
            for i in range(1024):
                seed ^= (seed << 13) & U64
                seed ^= seed >> 7
                seed ^= (seed << 17) & U64
                vector.append(((seed + i) & U64) % 2000 / 1000.0 - 1.0)
            out.append(vector)
        return out


@pytest.fixture
def clean():
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLES}")


@pytest.fixture
def store(clean):
    pool = ConnectionPool(DB_URL, min_size=0, max_size=2, kwargs={"row_factory": dict_row}, open=True)
    with pool.connection() as conn:
        migrate(conn)
    yield MemoryStore(pool, StubEmbed)
    pool.close()


def test_migrations_adopt_a_rust_database_and_are_idempotent(clean):
    rust_schema = migrations()[0][2]
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(rust_schema)
        conn.execute(
            "INSERT INTO memories (id, subject, content) VALUES ('m1', 's', 'kept'), ('m2', 's', 'kept too')"
        )
        conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES "
            "('m1', 'm2', 'extends'), ('m1', 'gone', 'extends'), ('gone', 'm2', 'extends')"
        )
        assert migrate(conn) == [1, 2]
        assert migrate(conn) == []
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 2
        assert conn.execute("SELECT from_id, to_id FROM edges").fetchall() == [("m1", "m2")]


def test_roundtrip(store):
    fox = store.store("test.fox", "the quick brown fox jumps over the lazy dog", confidence=0.9)
    assert store.store("test.fox", "the quick brown fox jumps over the lazy dog") == fox
    cat = store.store("test.cat", "a sleepy cat naps in the afternoon sun")
    assert len(fox) == 26 and fox == fox.lower()

    hits = store.recall("quick brown fox", limit=5)
    assert hits[0]["memory"]["id"] == fox and hits[0]["score"] > 0
    assert [h["memory"]["id"] for h in store.recall("fox", subject="test.cat")] == [cat]

    [memory] = store.get([fox])
    assert memory["confidence"] == 0.8999999761581421
    assert memory["classification"] == "standard" and memory["metadata"] == {}
    assert [m["id"] for m in store.get([cat, "nope", fox, cat])] == [cat, fox]

    store.link(cat, fox, "related_to", 0.5)
    nodes = store.graph(fox, 2)
    assert [(n["memory"]["id"], n["depth"]) for n in nodes] == [(fox, 0), (cat, 1)]
    assert nodes[0]["edges"][0]["weight"] == 0.5


def test_core_memories_are_permanent(store):
    m = store.store("test.core", "never forget this", classification="core")
    store.classify(m, "core")  # no-op
    with pytest.raises(StoreError, match="is core and cannot change"):
        store.classify(m, "standard")
    with pytest.raises(StoreError, match="is core and cannot be forgotten"):
        store.forget(m)
    s = store.store("test.core", "promote me")
    store.classify(s, "core")
    assert store.get([s])[0]["classification"] == "core"
    with pytest.raises(StoreError, match="not found: nope"):
        store.classify("nope", "core")


def test_forget_removes_edges_and_link_needs_both_memories(store):
    a = store.store("t", "alpha memory")
    b = store.store("t", "beta memory")
    with pytest.raises(StoreError, match="not found: nope"):
        store.link(a, "nope", "extends")
    store.link(a, b, "extends")
    assert store.forget(a) is True
    assert store.forget(a) is False
    assert store.graph(b, 2) == [{"memory": store.get([b])[0], "edges": [], "depth": 0}]
