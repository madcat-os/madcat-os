"""madcat — the MADCAT command line.

madcat mcp serve [--transport stdio|http] [--bind HOST:PORT]
madcat mcp list [--json]
madcat mcp call <tool> ['<json>']
madcat status

stdio and ``mcp call`` run as the local user (``MADCAT_LOCAL_DB_URL``); HTTP requires OAuth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from enum import StrEnum
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from madcat_os import __version__
from madcat_os.config import SERVER_NAME, Settings, settings

app = typer.Typer(
    name="madcat",
    help="MADCAT MCP server: OAuth-first, every user on their own database.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
mcp_app = typer.Typer(help="MCP server and tool registry", no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")

# stdout is the MCP wire on stdio; everything human goes to stderr.
console = Console(stderr=True)
out = Console()


class Transport(StrEnum):
    stdio = "stdio"
    http = "http"


def _version(value: bool) -> None:
    if value:
        out.print(f"madcat {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool, typer.Option("--version", "-V", callback=_version, is_eager=True, help="Print version")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _set(value: object) -> str:
    return "[green]set[/]" if value else "[yellow]not set[/]"


@app.command()
def status() -> None:
    """Show effective configuration and tool count."""
    from madcat_os.server import build_server

    cfg = settings()
    tools = asyncio.run(build_server(cfg).list_tools())
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("server", f"{SERVER_NAME} {__version__}")
    table.add_row("http bind", f"{cfg.host}:{cfg.port}")
    table.add_row("public url", cfg.public_url or "[yellow]not set[/]")
    table.add_row("oauth issuer", cfg.oauth_issuer or "[yellow]not set[/]")
    table.add_row("introspection secret", _set(cfg.oauth_introspect_secret))
    table.add_row("allowed", ", ".join(cfg.oauth_allowed) or "[yellow]nobody[/]")
    table.add_row(
        "http ready", "[green]yes[/]" if cfg.oauth_configured else "[red]no (OAuth not configured)[/]"
    )
    table.add_row("local database", _set(cfg.local_db_url))
    table.add_row("tools", str(len(tools)))
    out.print(Panel(table, title="madcat", border_style="cyan"))


@mcp_app.command("list")
def mcp_list(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Tool inventory."""
    from madcat_os.server import build_server

    tools = asyncio.run(build_server().list_tools())
    if as_json:
        out.print_json(json.dumps([t.model_dump(mode="json") for t in tools]))
        return
    table = Table(title=f"{SERVER_NAME} tools ({len(tools)})", box=box.SIMPLE_HEAD)
    table.add_column("tool", style="bold cyan", no_wrap=True)
    table.add_column("description")
    for t in tools:
        table.add_row(t.name, (t.description or "").strip().splitlines()[0] if t.description else "")
    out.print(table)


@mcp_app.command("call")
def mcp_call(
    name: Annotated[str, typer.Argument(help="Tool name")],
    args: Annotated[str, typer.Argument(help="JSON object of arguments")] = "{}",
) -> None:
    """Run one tool locally as the local user, no MCP client needed."""
    from madcat_os.server import build_server
    from madcat_os.users import enable_local_user

    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as e:
        console.print(f"[red]bad JSON arguments:[/] {e}")
        raise typer.Exit(2) from None
    if not isinstance(parsed, dict):
        console.print("[red]arguments must be a JSON object[/]")
        raise typer.Exit(2)

    enable_local_user()
    try:
        result = asyncio.run(build_server().call_tool(name, parsed))
    except Exception as e:  # noqa: BLE001 — unknown tool or bad arguments
        console.print(f"[red]{name} failed:[/] {e}")
        raise typer.Exit(1) from None

    for block in result.content:
        text = getattr(block, "text", None)
        try:
            out.print(JSON(text) if text is not None else block)
        except Exception:  # noqa: BLE001 — plain text, not JSON
            out.print(text)
    if result.is_error:
        raise typer.Exit(1)


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[
        Transport, typer.Option("--transport", "-t", help="stdio or http")
    ] = Transport.stdio,
    bind: Annotated[
        str | None, typer.Option("--bind", help="HOST:PORT for http (default from config)")
    ] = None,
) -> None:
    """Run the MCP server. HTTP requires OAuth (madcat-web); stdio runs as the local user."""
    from madcat_os.server import serve_http, serve_stdio

    overrides: dict = {}
    if bind:
        host, _, port = bind.rpartition(":")
        if not host or not port.isdigit():
            console.print(f"[red]--bind must be HOST:PORT, got {bind!r}[/]")
            raise typer.Exit(2)
        overrides.update(host=host, port=int(port))
    cfg = Settings(**overrides) if overrides else settings()

    if transport is Transport.stdio:
        console.print(f"[dim]{SERVER_NAME} {__version__} serving on stdio[/]")
        serve_stdio(cfg)
        return

    if not cfg.oauth_configured:
        console.print(
            "[red]HTTP needs OAuth:[/] set MADCAT_PUBLIC_URL, MADCAT_OAUTH_ISSUER and "
            "MADCAT_OAUTH_INTROSPECT_SECRET (or [mcp] public_url, oauth_issuer, oauth_introspect_secret)"
        )
        raise typer.Exit(2)
    console.print(
        Panel(
            f"[bold]{SERVER_NAME}[/] {__version__}\n"
            f"http://{cfg.host}:{cfg.port}/mcp\n"
            f"resource: {cfg.resource_url}\n"
            f"issuer: {cfg.oauth_issuer}\n"
            f"allowed: {', '.join(cfg.oauth_allowed) or 'nobody'}",
            title="madcat serve",
            border_style="cyan",
            expand=False,
        )
    )
    serve_http(cfg)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
