import json

import httpx
import pytest

from madcat_os import config
from madcat_os.config import EmbeddingConfig
from madcat_os.memory import EmbeddingError, OllamaEmbedder, OpenAIEmbedder, create_embedder, traverse
from madcat_os.memory.store import vector_literal
from madcat_os.server import build_server
from madcat_os.tools.memory import render


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("EEMS_PG_DSN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.madcat_toml.cache_clear()
    yield tmp_path
    config.madcat_toml.cache_clear()


def write_config(home, text):
    path = home / ".config" / "madcat" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    config.madcat_toml.cache_clear()


# ── graph traversal ───────────────────────────────────────


def edge(a, b):
    return {"from_id": a, "to_id": b, "relation": "related_to", "weight": 1.0, "created_at": 0}


def run_traverse(edges, root, depth, missing=()):
    def get_memory(i):
        return None if i in missing else {"id": i}

    def get_edges(i):
        return [e for e in edges if i in (e["from_id"], e["to_id"])]

    return [(n["memory"]["id"], n["depth"]) for n in traverse(root, depth, get_memory, get_edges)]


def test_traverse_is_depth_first_from_the_last_pushed_edge():
    edges = [edge("a", "b"), edge("a", "c"), edge("c", "d")]
    assert run_traverse(edges, "a", 2) == [("a", 0), ("c", 1), ("d", 2), ("b", 1)]
    assert run_traverse(edges, "a", 1) == [("a", 0), ("c", 1), ("b", 1)]
    assert run_traverse(edges, "a", 0) == [("a", 0)]


def test_traverse_reports_the_depth_it_reached_a_node_at():
    # a-b, a-c, c-b: b is one hop from a, but the stack reaches it through c first.
    edges = [edge("a", "b"), edge("a", "c"), edge("c", "b")]
    assert run_traverse(edges, "a", 2) == [("a", 0), ("c", 1), ("b", 2)]


def test_traverse_survives_cycles_and_missing_nodes():
    edges = [edge("a", "b"), edge("b", "a"), edge("b", "ghost")]
    assert run_traverse(edges, "a", 5, missing={"ghost"}) == [("a", 0), ("b", 1)]
    assert run_traverse(edges, "ghost", 2, missing={"ghost"}) == []


# ── rendering ─────────────────────────────────────────────


def test_render_matches_serde_pretty_with_sorted_keys():
    text = render([{"subject": "zażółć", "count": 3}]).text
    assert text == '[\n  {\n    "count": 3,\n    "subject": "zażółć"\n  }\n]'


def test_vector_literal():
    assert vector_literal([0.5, -1, 0.25]) == "[0.5,-1.0,0.25]"


# ── embedders ─────────────────────────────────────────────


def recorder(response):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=response)

    return seen, httpx.MockTransport(handler)


def test_ollama_request_shape():
    seen, transport = recorder({"embeddings": [[0.1, "x", 2]]})
    vectors = OllamaEmbedder("http://h:11434/", "bge-m3:567m", transport=transport).embed(["hi"])
    assert vectors == [[0.1, 2.0]]
    assert seen["url"] == "http://h:11434/api/embed"
    assert seen["body"] == {"model": "bge-m3:567m", "input": ["hi"], "keep_alive": -1}


def test_openai_request_shape():
    seen, transport = recorder({"data": [{"index": 0, "embedding": [1, 2]}]})
    embedder = OpenAIEmbedder("http://h:11434", "bge-m3", api_key="k", transport=transport)
    assert embedder.embed(["hi"]) == [[1.0, 2.0]]
    assert seen["url"] == "http://h:11434/v1/embeddings"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {"model": "bge-m3", "input": ["hi"]}


def test_embedder_errors_read_like_rust():
    _, transport = recorder({"nope": 1})
    with pytest.raises(EmbeddingError, match="^embedding: missing 'embeddings' in response$"):
        OllamaEmbedder("http://h", "m", transport=transport).embed(["hi"])
    with pytest.raises(EmbeddingError, match=r"\[embedding\] url is required"):
        create_embedder(EmbeddingConfig())


# ── config ────────────────────────────────────────────────


def test_embedding_config_and_dsn(home, monkeypatch):
    assert config.eems_pg_dsn() is None
    write_config(
        home,
        '[database]\ndsn = "postgres://cfg/eems"\n\n'
        '[embedding]\nprovider = "openai"\nurl = "http://e#x"\nmodel = "m"\n',
    )
    assert config.eems_pg_dsn() == "postgres://cfg/eems"
    monkeypatch.setenv("EEMS_PG_DSN", "postgres://env/eems")
    assert config.eems_pg_dsn() == "postgres://env/eems"
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    cfg = config.embedding_config()
    assert cfg == EmbeddingConfig(provider="openai", url="http://e#x", model="m", api_key="sk")
    assert isinstance(create_embedder(cfg), OpenAIEmbedder)
    assert isinstance(create_embedder(EmbeddingConfig(provider="ollama", url="http://e")), OllamaEmbedder)


# ── tools ─────────────────────────────────────────────────

RUST_ARGS = {
    "memory_store": ({"subject", "content"}, {"classification", "confidence", "namespace"}),
    "memory_recall": ({"query"}, {"limit", "subject", "namespace"}),
    "memory_get": ({"ids"}, set()),
    "memory_forget": ({"id"}, set()),
    "memory_classify": ({"id"}, {"classification"}),
    "memory_list": (set(), {"namespace"}),
    "memory_link": ({"from_id", "to_id", "relation"}, {"weight"}),
    "memory_graph": ({"id"}, {"depth"}),
}


async def test_memory_tools_match_the_rust_arguments():
    tools = {t.name: t for t in await build_server().list_tools()}
    for name, (required, optional) in RUST_ARGS.items():
        schema = tools[name].input_schema
        assert set(schema.get("required", [])) == required, name
        assert set(schema["properties"]) == required | optional, name
        assert tools[name].output_schema is None, name


async def test_missing_dsn_is_a_rust_style_error():
    result = await build_server().call_tool("memory_list", {})
    assert result.is_error
    assert json.loads(result.content[0].text) == {
        "error": "No Postgres DSN — set [database].dsn in ~/.config/madcat/config.toml or EEMS_PG_DSN"
    }
