"""Embedding providers: Ollama ``/api/embed`` and OpenAI-compatible ``/v1/embeddings``.

One vector per input text, as the endpoint returns it: no truncation, no normalization. The
``vector(1024)`` column is what enforces the dimension.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from madcat_os.config import EmbeddingConfig, embedding_config

# httpx logs every request at INFO; on stdio that is stderr noise per recall.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Rust sets no timeout; a cold model load can take a while, but a dead endpoint must not hang a tool.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class EmbeddingError(Exception):
    def __str__(self) -> str:
        return f"embedding: {super().__str__()}"


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _floats(v: object) -> list[float]:
    if not isinstance(v, list):
        raise EmbeddingError("embedding is not an array")
    # Non-numbers are dropped, as the Rust provider does.
    return [float(x) for x in v if isinstance(x, int | float) and not isinstance(x, bool)]


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, transport: httpx.BaseTransport | None = None) -> None:
        self.url = f"{base_url.rstrip('/')}/api/embed"
        self.model_id = model
        self._client = httpx.Client(timeout=TIMEOUT, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.model_id, "input": texts, "keep_alive": -1}
        try:
            r = self._client.post(self.url, json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbeddingError(f"ollama request failed: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise EmbeddingError(f"ollama response parse failed: {e}") from e
        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(embeddings, list):
            raise EmbeddingError("missing 'embeddings' in response")
        return [_floats(v) for v in embeddings]


class OpenAIEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/v1/embeddings"
        self.model_id = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(timeout=TIMEOUT, headers=headers, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = self._client.post(self.url, json={"model": self.model_id, "input": texts})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbeddingError(f"openai embed request failed: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise EmbeddingError(f"openai embed response parse failed: {e}") from e
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise EmbeddingError("missing 'data' in response")
        out = []
        for item in items:
            if not isinstance(item, dict) or "embedding" not in item:
                raise EmbeddingError("missing 'embedding' in data item")
            out.append(_floats(item["embedding"]))
        return out


def create_embedder(cfg: EmbeddingConfig | None = None) -> Embedder:
    cfg = cfg or embedding_config()
    if not cfg.url:
        raise EmbeddingError("[embedding] url is required (ollama or openai-compat endpoint)")
    if cfg.provider == "openai":
        return OpenAIEmbedder(cfg.url, cfg.model or "bge-m3", cfg.api_key)
    return OllamaEmbedder(cfg.url, cfg.model or "bge-m3:567m")
