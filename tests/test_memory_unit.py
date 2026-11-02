import json

import httpx
import pytest

from madcat_os.config import EmbeddingConfig
from madcat_os.db.migrate import migrations
from madcat_os.memory import EmbeddingError, MemoryStore, OllamaEmbedder, OpenAIEmbedder, StoreError, traverse
from madcat_os.memory.embed import create_embedder
from madcat_os.memory.store import vector_literal
from madcat_os.tools._run import render


class NoPool:
    """Validation must fail before any database work."""

    def connection(self):
        raise AssertionError("touched the database")


store = MemoryStore(NoPool(), lambda: None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda: store.store("s", "c", classification="sacred"), "classification must be"),
        (lambda: store.store("s", "c", confidence=1.5), "confidence must be between 0 and 1"),
        (lambda: store.recall("  "), "query must not be empty"),
        (lambda: store.recall("q", limit=0), "limit must be at least 1"),
        (lambda: store.classify("x", "sacred"), "classification must be"),
        (lambda: store.link("a", "b", "loves"), "relation must be one of"),
        (lambda: store.link("a", "a", "extends"), "cannot link to itself"),
        (lambda: store.link("a", "b", "extends", weight=-0.1), "weight must be between 0 and 1"),
        (lambda: store.graph("a", -1), "depth must be 0 or more"),
    ],
)
def test_invalid_input_is_refused_before_the_database(call, message):
    with pytest.raises(StoreError, match=message):
        call()


def test_get_nothing_skips_the_database():
    assert store.get([]) == []


def edge(a, b):
    return {"from_id": a, "to_id": b, "relation": "related_to", "weight": 1.0, "created_at": 0}


def run_traverse(edges, root, depth, missing=()):
    def get_edges(i):
        return [e for e in edges if i in (e["from_id"], e["to_id"])]

    nodes = traverse(root, depth, lambda i: None if i in missing else {"id": i}, get_edges)
    return [(n["memory"]["id"], n["depth"]) for n in nodes]


def test_traverse_order_cycles_and_missing_nodes():
    edges = [edge("a", "b"), edge("a", "c"), edge("c", "d")]
    assert run_traverse(edges, "a", 2) == [("a", 0), ("c", 1), ("d", 2), ("b", 1)]
    assert run_traverse(edges, "a", 0) == [("a", 0)]
    cyclic = [edge("a", "b"), edge("b", "a"), edge("b", "ghost")]
    assert run_traverse(cyclic, "a", 5, missing={"ghost"}) == [("a", 0), ("b", 1)]
    assert run_traverse(cyclic, "ghost", 2, missing={"ghost"}) == []


def test_render_is_sorted_pretty_json():
    assert (
        render([{"subject": "zażółć", "count": 3}]).text
        == '[\n  {\n    "count": 3,\n    "subject": "zażółć"\n  }\n]'
    )
    assert vector_literal([0.5, -1, 0.25]) == "[0.5,-1.0,0.25]"


def test_migrations_are_bundled_in_order():
    assert [(v, n) for v, n, _ in migrations()] == [(1, "init"), (2, "orphan_edges")]


def recorder(response):
    seen = {}

    def handler(request):
        seen.update(
            url=str(request.url), auth=request.headers.get("authorization"), body=json.loads(request.content)
        )
        return httpx.Response(200, json=response)

    return seen, httpx.MockTransport(handler)


def test_embedders_request_shapes():
    seen, transport = recorder({"embeddings": [[0.1, "x", 2]]})
    assert OllamaEmbedder("http://h:11434/", "m", transport=transport).embed(["hi"]) == [[0.1, 2.0]]
    assert seen == {
        "url": "http://h:11434/api/embed",
        "auth": None,
        "body": {"model": "m", "input": ["hi"], "keep_alive": -1},
    }
    seen, transport = recorder({"data": [{"embedding": [1, 2]}]})
    assert OpenAIEmbedder("http://h", "m", api_key="k", transport=transport).embed(["hi"]) == [[1.0, 2.0]]
    assert seen["url"] == "http://h/v1/embeddings" and seen["auth"] == "Bearer k"
    with pytest.raises(EmbeddingError, match=r"\[embedding\] url is required"):
        create_embedder(EmbeddingConfig())
