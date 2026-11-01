# madcat-os (binary: `madcat`)

MADCAT MCP server and command line in Python. The installed binary is `madcat`; one package, three faces:

- `madcat mcp serve` — MCP over **stdio** (default) or **Streamable HTTP** at `/mcp`
- `madcat mcp list` / `mcp call <tool> '<json>'` — drive the tool registry from the shell
- `madcat status` — effective config and tool count
- `madcat claude hooks pre-tool inject_session_id` / `session-start welcome` — Claude Code hooks

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

Hooks, in `~/.claude/settings.json` (event JSON on stdin, hook output on stdout, always exit 0):

```json
"hooks": {
  "PreToolUse": [ { "matcher": "", "hooks": [
    { "type": "command", "command": "madcat claude hooks pre-tool inject_session_id", "timeout": 5 } ] } ],
  "SessionStart": [ { "hooks": [
    { "type": "command", "command": "madcat claude hooks session-start welcome", "timeout": 5 } ] } ] }
```

`inject_session_id` records the session in `~/.claude/current-session-id` and `current-session.json`
(`MADCAT_CLAUDE_STATE_DIR`, `[claude].state_dir`) and adds `session_id` to madcat tool calls
(`--server` / `MADCAT_CLAUDE_MCP_SERVER` when the tool name does not identify madcat). `welcome`
prints the boot prompt for the active persona cart (`~/.local/share/madcat/tts-state.toml` →
`~/.config/madcat/carts/<tag>.toml`); `MADCAT_CLAUDE_WELCOME`, `[claude].welcome` or
`$CLAUDE_PLUGIN_DATA/welcome.md` replaces it.

## Configuration

Environment, prefix `MADCAT_MCP_` (or a `.env` file): `HOST` (127.0.0.1), `PORT` (4097), `TOKEN`, `LOG_LEVEL`.

The `memory_*` tools use the same Postgres + pgvector database and `~/.config/madcat/config.toml`
as the Rust madcat: `EEMS_PG_DSN` or `[database].dsn`, and `[embedding]` with `provider`
(`ollama` or `openai`), `url` (required), `model` and `api_key` (falls back to `OPENAI_API_KEY`).
The schema is created on first use if missing; pgvector must already be installed or creatable by
the role.
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
  config.py     Settings (pydantic-settings); hook/cart getters over ~/.config/madcat/config.toml
  claude_hooks.py  Claude Code PreToolUse / SessionStart handlers
  cart.py       persona carts: tts-state.toml and carts/*.toml
  memory/       EEMS store (Postgres + pgvector, hybrid recall), embedders, schema
  tools/        registry; one module per tool family
tests/
justfile
```
