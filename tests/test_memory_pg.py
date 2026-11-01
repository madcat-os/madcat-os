"""Live Postgres + pgvector roundtrip. Skipped unless EEMS_PG_DSN is set in the environment.

Writes only to a throwaway ``test-<ulid>`` namespace and deletes it afterwards. Embeddings come
from a deterministic stub, so no model endpoint is needed.
"""

import os

import pytest

from madcat_os.memory.store import PgStore, new_id

DSN = os.environ.get("EEMS_PG_DSN", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="EEMS_PG_DSN not set")

U64 = (1 << 64) - 1


class StubEmbed:
    """Port of the Rust test's StubEmbed: FNV-1a seed, xorshift, 1024 values in [-1, 1]."""

    model_id = "stub-1024"

    def embed(self, texts):
        out = []
        for text in texts:
            seed = 1469598103934665603
            for b in text.encode():
                seed ^= b
                seed = (seed * 1099511628211) & U64
            vector = []
            for i in range(1024):
                seed ^= (seed << 13) & U64
                seed ^= seed >> 7
                seed ^= (seed << 17) & U64
                vector.append(((seed + i) & U64) % 2000 / 1000.0 - 1.0)
            out.append(vector)
        return out


@pytest.fixture
def store():
    s = PgStore(DSN, StubEmbed())
    ns = f"test-{new_id()}"
    yield s, ns
    with s.pool.connection() as conn:
        conn.execute(
            "DELETE FROM edges WHERE from_id IN (SELECT id FROM memories WHERE namespace = %s) "
            "OR to_id IN (SELECT id FROM memories WHERE namespace = %s)",
            (ns, ns),
        )
        conn.execute("DELETE FROM memories WHERE namespace = %s", (ns,))
    s.close()


def test_store_and_recall_roundtrip(store):
    s, ns = store
    fox = s.store("test.fox", "the quick brown fox jumps over the lazy dog", namespace=ns, confidence=0.9)
    assert s.store("test.fox", "the quick brown fox jumps over the lazy dog", namespace=ns) == fox
    cat = s.store("test.cat", "a sleepy cat naps in the afternoon sun", namespace=ns)
    assert len(fox) == 26 and fox == fox.lower()

    hits = s.recall("quick brown fox", limit=5, namespace=ns)
    assert hits[0]["memory"]["id"] == fox and hits[0]["score"] > 0
    assert [h["memory"]["id"] for h in s.recall("fox", subject="test.cat", namespace=ns)] == [cat]

    [memory] = s.get([fox])
    assert memory["confidence"] == 0.8999999761581421  # f32 0.9 widened, as Rust prints it
    assert memory["classification"] == "standard" and memory["metadata"] == {}
    assert memory["created_at"] == memory["updated_at"]
    assert sorted(memory) == sorted(
        [
            "id",
            "namespace",
            "subject",
            "content",
            "classification",
            "confidence",
            "metadata",
            "created_at",
            "updated_at",
        ]
    )

    assert {r["subject"]: r["count"] for r in s.list_subjects(ns)} == {"test.fox": 1, "test.cat": 1}

    s.link(fox, cat, "related_to", 0.5)
    nodes = s.graph(fox, 2)
    assert [(n["memory"]["id"], n["depth"]) for n in nodes] == [(fox, 0), (cat, 1)]
    assert nodes[0]["edges"][0]["weight"] == 0.5

    s.classify(cat, "core")
    assert s.get([cat])[0]["classification"] == "core"
    assert s.forget(cat) is True
    assert s.forget(cat) is False
