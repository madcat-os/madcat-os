"""EEMS memory: Postgres + pgvector store with hybrid recall, shared with the Rust madcat."""

from madcat_os.memory.embed import Embedder, EmbeddingError, OllamaEmbedder, OpenAIEmbedder, create_embedder
from madcat_os.memory.store import PgStore, StoreError, default_store, traverse

__all__ = [
    "Embedder",
    "EmbeddingError",
    "OllamaEmbedder",
    "OpenAIEmbedder",
    "PgStore",
    "StoreError",
    "create_embedder",
    "default_store",
    "traverse",
]
