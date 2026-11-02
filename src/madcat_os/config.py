"""Runtime settings.

Everything comes from the environment, a regular ``.env`` file in the working directory, or
``~/.config/madcat/config.toml`` (shared with the Rust madcat), in that order. The environment names
and the ``[mcp]`` keys match the Rust server so one config drives both.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

SERVER_NAME = "madcat"

log = logging.getLogger("madcat_os")


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


def toml_get(path: str) -> Any:
    """Dot-path lookup into config.toml, e.g. ``mcp.oauth_issuer``."""
    cur: Any = madcat_toml()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _dotenv() -> Path | None:
    """``.env`` in the working directory, only when it is a regular file. pydantic-settings also
    reads FIFOs, and a named pipe ``.env`` (1Password mounts one) blocks until something writes to
    it, so the server would never get to the MCP handshake."""
    path = Path(".env")
    return path if path.is_file() else None


# Settings field → config.toml key, for values not set in the environment.
TOML_KEYS = {
    "host": "mcp.host",
    "port": "mcp.port",
    "public_url": "mcp.public_url",
    "oauth_issuer": "mcp.oauth_issuer",
    "oauth_introspect_secret": "mcp.oauth_introspect_secret",
    "oauth_allowed": "mcp.oauth_allowed",
    "local_db_url": "local.db_url",
    "log_level": "mcp.log_level",
}


class TomlSource(PydanticBaseSettingsSource):
    """config.toml values for fields the environment and .env leave unset (lowest priority)."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        key = TOML_KEYS.get(field_name)
        return (toml_get(key) if key else None), field_name, False

    def __call__(self) -> dict[str, Any]:
        out = {}
        for name, field in self.settings_cls.model_fields.items():
            value, _, _ = self.get_field_value(field, name)
            if value is not None:
                out[name] = value
        return out


def _split(v: Any) -> Any:
    return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MADCAT_", extra="ignore")

    host: str = Field(default="127.0.0.1", description="HTTP bind address")
    port: int = Field(default=4097, description="HTTP bind port")
    public_url: str | None = Field(
        default=None, description="This server's public origin, e.g. https://mcp.madcat-os.com"
    )
    oauth_issuer: str | None = Field(default=None, description="Authorization server (madcat-web) origin")
    oauth_introspect_secret: SecretStr | None = Field(
        default=None, description="Bearer secret for {issuer}/oauth/introspect"
    )
    oauth_allowed: Annotated[list[str], NoDecode] = Field(
        default_factory=list, description="Emails or subjects allowed through; '*' admits every user"
    )
    local_db_url: SecretStr | None = Field(
        default=None, description="Database of the single local user for stdio and `mcp call`"
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Host headers accepted by the HTTP transport (DNS-rebinding guard). "
        "Empty: only the bind host when binding loopback, any host otherwise.",
    )
    log_level: str = Field(default="info", description="uvicorn / logging level")

    def __init__(self, **values: Any) -> None:
        super().__init__(_env_file=_dotenv(), **values)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, dotenv_settings, TomlSource(settings_cls)

    @field_validator("oauth_allowed", "allowed_hosts", mode="before")
    @classmethod
    def _comma_separated(cls, v: Any) -> Any:
        return _split(v)

    @property
    def oauth_configured(self) -> bool:
        return bool(self.public_url and self.oauth_issuer and self.oauth_introspect_secret)

    @property
    def resource_url(self) -> str:
        """The MCP endpoint as an RFC 8707 resource, ``{public_url}/mcp``."""
        return f"{(self.public_url or '').rstrip('/')}/mcp"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class EmbeddingConfig:
    """``MADCAT_EMBED_*`` or ``[embedding]`` in config.toml. ``provider = "openai"`` picks the
    OpenAI-compatible API, anything else Ollama. ``api_key`` falls back to ``OPENAI_API_KEY``."""

    provider: str = "ollama"
    url: str | None = None
    model: str | None = None
    api_key: str | None = None


def embedding_config() -> EmbeddingConfig:
    def s(key: str) -> str | None:
        v = os.environ.get(f"MADCAT_EMBED_{key.upper()}") or toml_get(f"embedding.{key}")
        return v if isinstance(v, str) and v else None

    return EmbeddingConfig(
        provider="openai" if s("provider") == "openai" else "ollama",
        url=s("url"),
        model=s("model"),
        api_key=s("api_key") or os.environ.get("OPENAI_API_KEY") or None,
    )
