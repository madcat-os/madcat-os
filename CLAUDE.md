# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

madcat-os is the Python port of the Rust `madcat` MCP server (`~/Projects/madcat`). It installs a
binary named `madcat` whose command shapes match the Rust one. Features move over from the Rust repo
one chunk at a time: the Claude Code hooks and the EEMS memory tools are done.

## Commands

```bash
uv sync                                  # runtime + dev deps (CI uses `uv sync --locked`)
just check                               # what CI runs: ruff check, ruff format --check, pytest, `madcat mcp list` smoke test
just fmt                                 # ruff fix + format
uv run pytest tests/test_server.py::test_mcp_initialize -q   # one test
uv run madcat mcp list                   # tool inventory
uv run madcat mcp call <tool> '<json>'   # run one tool in-process, no MCP client
uv run madcat mcp serve --transport http --token t --bind 127.0.0.1:4197
just bump patch [dry]                    # edits pyproject.toml + uv.lock only; no commit, no tag
echo '{"session_id":"s","tool_name":"mcp__madcat__ping","tool_input":{}}' | uv run madcat claude hooks pre-tool inject_session_id
echo '{"source":"startup"}' | uv run madcat claude hooks session-start welcome   # --cart <tag> to pick one
```

Release: bump, commit, then `git tag -a vX.Y.Z` and push the tag. CI builds and publishes to PyPI
only for `v*` tags, and fails if the tag does not match `pyproject.toml`'s version. `uv.lock` is
tracked and must move with the version (the bump recipe handles it).

## Architecture

- `server.build_server()` is the only place the tool registry meets the `MCPServer` (from
  `mcp.server.mcpserver`, the v2 SDK naming). The CLI (`status`, `mcp list`, `mcp call`), the stdio
  runner and the HTTP app all go through it, so a registered tool shows up everywhere at once.
- HTTP transport: Streamable HTTP at `/mcp`, static bearer-token middleware (`/health` stays open),
  and a DNS-rebinding guard derived from the bind host (`transport_security`).
- Tools: one module per family in `src/madcat_os/tools/`, each exposing `register(mcp)` with
  `@mcp.tool()` handlers (docstring = tool description, type hints = schema), listed in `MODULES` in
  `tools/__init__.py`.
- Config: `config.Settings` (pydantic-settings, prefix `MADCAT_MCP_`, `.env` honoured). Tools take a
  `Settings`; they never read `os.environ` directly. `settings()` is `lru_cache`d.
- stdout is the MCP wire on stdio. Human output goes to stderr (`console` in `cli.py`); `out` is
  only for command results.
- Tests drive the CLI with Typer's `CliRunner` and the HTTP app in-process through
  `httpx.ASGITransport`; the lifespan and the client must live in one task (see `http_client()`).

## Porting from the Rust madcat

Read `~/Projects/madcat/AGENTS.md` and its `CLAUDE.md` for the source design. Keep tool names,
argument names, defaults and descriptions identical to the Rust tools: existing Claude Code setups,
hooks and stored prompts call them by name. Rust namespaces tools with `ns("prefix", …)` in
`src/tools/mod.rs`, so the Rust `store` in `src/tools/memory.rs` is the MCP tool `memory_store`.

### Claude Code hooks (ported)

`claude_hooks.py` is a port of the Rust `src/claude_hooks.rs`. Its welcome prompt matches the Rust
output except for one deliberate change: boot step 3 makes only the `user.preference` recall, and
the `core.self-model` and `cart.<tag>` recalls are gone. To check for other drift, diff
`session-start welcome` from both binaries. Persona and section keys are sorted to match Rust's
BTreeMap-backed TOML tables.

- Contract: read the event JSON from stdin, print only the hook's output, always exit 0. Errors go
  to stderr and never block a tool call or a session.
- Hooks run on every tool call, so `cli.py` imports the MCP SDK lazily inside each command.
  `is_madcat_tool` builds the server for its tool names only after the cheap checks fail.
- `inject_session_id` injects only for tools registered in this package (or for a server named by
  `--server` / `MADCAT_CLAUDE_MCP_SERVER`). The MCP SDK ignores the extra `session_id` argument.
  Nothing here reads it yet; the Rust `registry.rs` carries it into `CallCtx`.
- The hook and cart settings (`claude_state_dir`, `claude_welcome_override`, `carts_dir`,
  `tts_state_path`) are getters in `config.py`. They read the Rust env names and
  `~/.config/madcat/config.toml`, not `Settings`.

### Memory (EEMS) (ported, single DSN)

`memory/store.py` (`PgStore`) is a port of the Rust `src/madcat_memory/pg.rs`, and
`tools/memory.py` is a port of `src/tools/memory.rs`. Both servers share one Postgres + pgvector
database, so keep the SQL, the schema (`memory/pg_001_init.sql` is a verbatim copy of the Rust
migration, code-index tables included) and the JSON output in step with Rust. To check, compare
`memory_list` / `memory_get` / `memory_recall` text from this server with
`~/Projects/madcat/target/release/madcat mcp call` on the same DSN. Live tests:
`EEMS_PG_DSN=… uv run pytest tests/test_memory_pg.py` (stub embedder, throwaway `test-<ulid>`
namespace).

- Only `EEMS_PG_DSN` / `[database].dsn` is supported. Rust's dual-DSN (`EEMS_DSN_CORE` +
  `EEMS_DSN_DOUBLE`, chosen by agent role) and per-user `{db_name}_{db}` databases for OAuth
  callers need caller identity this server doesn't have yet.
- Output parity details:
  - Each tool returns one `CallToolResult` with a single text block:
    `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)`. That is serde's pretty print with
    sorted keys; returning a list would make the SDK split it into one block per item.
  - Errors are `{"error": …}` with `isError`, carrying Rust's messages.
  - `real` columns are selected `::float8`, so they print as Rust's f32 widened to f64.
- Rust behaviour kept as is: no validation of classification or relation, no enforced `core`
  immutability, dedup on (namespace, subject, content hash) before embedding, depth-first stack
  order in `graph` (`traverse`, unit-tested without Postgres).
- The store is synchronous (psycopg pool, not tied to an event loop); tools call it through
  `anyio.to_thread`. Embedding settings come from `[embedding]` (`provider`, `url`, `model`,
  `api_key` → `OPENAI_API_KEY`) via `config.embedding_config()`.
