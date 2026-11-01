"""Runtime settings. Everything comes from the environment (prefix MADCAT_MCP_) or a .env file.

Tools never read os.environ directly; they take a Settings instance.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_NAME = "madcat"


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
