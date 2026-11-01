"""madcat — the MADCAT command line.

madcat mcp serve [--transport stdio|http] [--bind HOST:PORT] [--token T]
madcat mcp list
madcat mcp call <tool> ['<json>']
madcat status
madcat claude hooks pre-tool <name> [--server S]
madcat claude hooks session-start <name> [--cart TAG]
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
    help="MADCAT — MCP server and tools, stdio or Streamable HTTP.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
mcp_app = typer.Typer(help="MCP server and tool registry", no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")
claude_app = typer.Typer(help="Claude Code integration", no_args_is_help=True)
hooks_app = typer.Typer(
    help="Hook handlers for ~/.claude/settings.json (event JSON on stdin, hook JSON on stdout)",
    no_args_is_help=True,
)
claude_app.add_typer(hooks_app, name="hooks")
app.add_typer(claude_app, name="claude")

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


@app.command()
def status() -> None:
    """Show effective configuration and tool count."""
    from madcat_os.server import build_server

    cfg = settings()
    mcp = build_server(cfg)
    tools = asyncio.run(mcp.list_tools())
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("server", f"{SERVER_NAME} {__version__}")
    table.add_row("python", sys.version.split()[0])
    table.add_row("http bind", f"{cfg.host}:{cfg.port}")
    table.add_row("token", "[green]set[/]" if cfg.token else "[yellow]none[/]")
    table.add_row("tools", str(len(tools)))
    out.print(Panel(table, title="madcat", border_style="cyan"))


@mcp_app.command("list")
def mcp_list(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Tool inventory."""
    from madcat_os.server import build_server

    mcp = build_server()
    tools = asyncio.run(mcp.list_tools())
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
    """Run one tool locally, no MCP client needed."""
    from madcat_os.server import build_server

    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as e:
        console.print(f"[red]bad JSON arguments:[/] {e}")
        raise typer.Exit(2) from None
    if not isinstance(parsed, dict):
        console.print("[red]arguments must be a JSON object[/]")
        raise typer.Exit(2)

    mcp = build_server()
    try:
        result = asyncio.run(mcp.call_tool(name, parsed))
    except Exception as e:  # noqa: BLE001 — surface the tool error, then exit non-zero
        console.print(f"[red]{name} failed:[/] {e}")
        raise typer.Exit(1) from None

    if result.structured_content is not None:
        out.print(JSON.from_data(result.structured_content))
        return
    for block in result.content:
        text = getattr(block, "text", None)
        if text is None:
            out.print(block)
            continue
        try:
            out.print(JSON(text))
        except Exception:  # noqa: BLE001 — plain text, not JSON
            out.print(text)


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[
        Transport, typer.Option("--transport", "-t", help="stdio or http")
    ] = Transport.stdio,
    bind: Annotated[str | None, typer.Option("--bind", help="HOST:PORT for http (default from env)")] = None,
    token: Annotated[
        str | None, typer.Option("--token", help="Bearer token for http", envvar="MADCAT_MCP_TOKEN")
    ] = None,
) -> None:
    """Run the MCP server."""
    from madcat_os.server import build_server

    overrides: dict = {}
    if bind:
        host, _, port = bind.rpartition(":")
        if not host or not port.isdigit():
            console.print(f"[red]--bind must be HOST:PORT, got {bind!r}[/]")
            raise typer.Exit(2)
        overrides.update(host=host, port=int(port))
    if token:
        overrides["token"] = token
    cfg = Settings(**overrides) if overrides else settings()
    mcp = build_server(cfg)

    if transport is Transport.stdio:
        from madcat_os.server import serve_stdio

        console.print(f"[dim]{SERVER_NAME} {__version__} serving on stdio[/]")
        serve_stdio(mcp)
        return

    from madcat_os.server import serve_http

    console.print(
        Panel(
            f"[bold]{SERVER_NAME}[/] {__version__}\n"
            f"http://{cfg.host}:{cfg.port}/mcp\n"
            f"token: {'[green]set[/]' if cfg.token else '[yellow]none[/]'}",
            title="madcat serve",
            border_style="cyan",
            expand=False,
        )
    )
    serve_http(mcp, cfg)


@hooks_app.command("pre-tool")
def hooks_pre_tool(
    name: Annotated[str, typer.Argument(help="Named hook to run")],
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            help="MCP server name the madcat tools are registered under, when it is not derivable "
            "from the tool name ([claude].mcp_server_name)",
        ),
    ] = None,
) -> None:
    """PreToolUse handler. Names: inject_session_id"""
    from madcat_os import claude_hooks

    claude_hooks.pre_tool(name, server)


@hooks_app.command("session-start")
def hooks_session_start(
    name: Annotated[str, typer.Argument(help="Named hook to run")],
    cart: Annotated[
        str | None,
        typer.Option(
            "--cart", help="Persona cart tag to boot with instead of the active one in tts-state.toml"
        ),
    ] = None,
) -> None:
    """SessionStart handler. Names: welcome (boot prompt from the active persona cart)"""
    from madcat_os import claude_hooks

    claude_hooks.session_start(name, cart)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
