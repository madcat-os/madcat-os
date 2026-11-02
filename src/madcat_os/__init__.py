"""MADCAT MCP server: OAuth-first, every user on their own database."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("madcat-os")
except PackageNotFoundError:  # running from a checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
