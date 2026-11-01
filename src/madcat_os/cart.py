"""Persona cart helpers: tts-state.toml (active cart) and carts/*.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from madcat_os.config import carts_dir, tts_state_path


def read_table(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def read_state() -> dict[str, Any]:
    return read_table(tts_state_path()) or {}


def active_cart_tag() -> str | None:
    """Active cart tag from tts-state.toml."""
    tag = read_state().get("active_cart")
    return tag if isinstance(tag, str) else None


def cart_path(tag: str) -> Path:
    return carts_dir() / f"{tag}.toml"


def load_cart(tag: str) -> dict[str, Any] | None:
    return read_table(cart_path(tag))


def list_cart_tags() -> list[str]:
    """Every ``<tag>.toml`` in the carts dir, sorted. Raises OSError when the dir is unreadable."""
    return sorted(p.name.removesuffix(".toml") for p in carts_dir().iterdir() if p.name.endswith(".toml"))
