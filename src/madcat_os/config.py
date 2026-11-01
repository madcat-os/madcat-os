"""Runtime settings.

Server settings come from the environment (prefix MADCAT_MCP_) or a .env file; tools never read
os.environ directly, they take a Settings instance. The Claude Code hooks and persona carts share
``~/.config/madcat/config.toml`` and their env names with the Rust madcat, as do the EEMS memory
store and its embedder, so their getters live here too, with the same precedence: env var →
config.toml → default.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_NAME = "madcat"

log = logging.getLogger("madcat_os")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MADCAT_MCP_", env_file=".env", extra="ignore")

    host: str = Field(default="127.0.0.1", description="HTTP bind address")
    port: int = Field(default=4097, description="HTTP bind port")
    token: str | None = Field(default=None, description="Static bearer token for the HTTP transport")
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Host headers accepted by the HTTP transport (DNS-rebinding guard). "
        "Empty: only the bind host when binding loopback, any host otherwise.",
    )
    log_level: str = Field(default="info", description="uvicorn / logging level")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def config_path() -> Path:
    return Path.home() / ".config" / "madcat" / "config.toml"


@lru_cache(maxsize=1)
def madcat_toml() -> dict[str, Any]:
    """The parsed config.toml; empty when it is missing or does not parse."""
    try:
        return tomllib.loads(config_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        log.warning("%s: parse failed, using defaults: %s", config_path(), e)
        return {}


def get_str(path: str) -> str | None:
    """Dot-path string lookup into config.toml, e.g. ``claude.state_dir``."""
    cur: Any = madcat_toml()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def expand_home(p: str) -> Path:
    return Path(p).expanduser()


def carts_dir() -> Path:
    return Path.home() / ".config" / "madcat" / "carts"


def tts_state_path() -> Path:
    return Path.home() / ".local" / "share" / "madcat" / "tts-state.toml"


def claude_state_dir() -> Path:
    """Where the Claude Code hooks keep ``current-session-id`` and ``current-session.json``:
    ``MADCAT_CLAUDE_STATE_DIR`` → ``[claude].state_dir`` → ``~/.claude``."""
    value = _env("MADCAT_CLAUDE_STATE_DIR") or get_str("claude.state_dir")
    return expand_home(value) if value else Path.home() / ".claude"


def claude_mcp_server_name() -> str | None:
    """MCP server name Claude Code registers madcat under, for hosts where the tool name alone does
    not identify it: ``MADCAT_CLAUDE_MCP_SERVER`` → ``[claude].mcp_server_name``."""
    return _env("MADCAT_CLAUDE_MCP_SERVER") or get_str("claude.mcp_server_name")


def claude_welcome_override() -> Path | None:
    """Operator-written replacement for the SessionStart welcome prompt: ``MADCAT_CLAUDE_WELCOME`` →
    ``[claude].welcome`` → ``$CLAUDE_PLUGIN_DATA/welcome.md``. Only returned when the file exists."""
    value = _env("MADCAT_CLAUDE_WELCOME") or get_str("claude.welcome")
    if value:
        path = expand_home(value)
    elif plugin_data := _env("CLAUDE_PLUGIN_DATA"):
        path = Path(plugin_data) / "welcome.md"
    else:
        return None
    return path if path.is_file() else None


def eems_pg_dsn() -> str | None:
    """Postgres DSN for EEMS memory: ``EEMS_PG_DSN`` → ``[database].dsn``."""
    return _env("EEMS_PG_DSN") or get_str("database.dsn")


@dataclass(frozen=True)
class EmbeddingConfig:
    """``[embedding]`` in config.toml. ``provider = "openai"`` picks the OpenAI-compatible API,
    anything else Ollama. ``api_key`` falls back to ``OPENAI_API_KEY``."""

    provider: str = "ollama"
    url: str | None = None
    model: str | None = None
    api_key: str | None = None


def embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="openai" if get_str("embedding.provider") == "openai" else "ollama",
        url=get_str("embedding.url"),
        model=get_str("embedding.model"),
        api_key=get_str("embedding.api_key") or _env("OPENAI_API_KEY"),
    )
