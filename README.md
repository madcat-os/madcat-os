# madcat-os (binary: `madcat`)

MADCAT MCP server and command line in Python. The installed binary is `madcat`; one package, three faces:

- `madcat mcp serve` — MCP over **stdio** (default) or **Streamable HTTP** at `/mcp`
- `madcat mcp list` / `mcp call <tool> '<json>'` — drive the tool registry from the shell
- `madcat status` — effective config and tool count

Functionality is being moved here from the Rust `madcat` binary; command shapes match it.

## Quick start

```bash
uv sync
uv run madcat status
uv run madcat mcp list
uv run madcat mcp call server_info
uv run madcat mcp serve --transport http --token t --bind 127.0.0.1:4197
```

Then POST an `initialize` to `http://127.0.0.1:4197/mcp` with `Authorization: Bearer t` and
`Accept: application/json, text/event-stream`; reuse the returned `mcp-session-id`.

## Claude Code

stdio:

```json
{ "mcpServers": { "madcat": { "command": "uv", "args": ["run", "--directory", "/path/to/madcat-os", "madcat", "mcp", "serve"] } } }
```

http:

```json
{ "mcpServers": { "madcat": { "type": "http", "url": "http://127.0.0.1:4097/mcp",
  "headers": { "Authorization": "Bearer ${MADCAT_MCP_TOKEN}" } } } }
```

## Configuration

Environment, prefix `MADCAT_MCP_` (or a `.env` file): `HOST` (127.0.0.1), `PORT` (4097), `TOKEN`, `LOG_LEVEL`.
`--bind` and `--token` on `mcp serve` override.

## Adding a tool

Create `src/madcat_os/tools/<module>.py` with `register(mcp: MCPServer)` and `@mcp.tool()` handlers
(docstring per tool, type hints per argument), then add `<module>.register` to `MODULES` in
`src/madcat_os/tools/__init__.py`. Human output goes to stderr; stdout is the stdio wire.

## Layout

```
src/madcat_os/
  cli.py        Typer app: status, mcp list|call|serve
  server.py     build_server(), HTTP app with bearer auth + /health, stdio/http runners
  config.py     Settings (pydantic-settings)
  tools/        registry; one module per tool family
tests/
justfile
```
