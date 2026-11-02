# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

madcat-os v2 is an OAuth-first MCP server (binary `madcat`). Over HTTP every request carries a
madcat-web access token and every tool runs against that user's own Postgres database; over stdio
and `madcat mcp call` it runs as one configured local user. v1 (a piecemeal port of the Rust
`~/Projects/madcat`, with Claude Code hooks and a shared DSN) was dropped; tool and argument names
still match the Rust server.

## Commands

```bash
uv sync                                  # runtime + dev deps (CI uses `uv sync --locked`)
just check                               # what CI runs: ruff check, ruff format --check, pytest, `madcat mcp list` smoke test
just fmt                                 # ruff fix + format
uv run pytest tests/test_http.py::test_tools_run_as_the_token_user -q   # one test
uv run madcat status                     # effective config, whether HTTP/OAuth is ready
uv run madcat mcp call whoami            # runs as the local user (MADCAT_LOCAL_DB_URL for memory tools)
MADCAT_TEST_DB_URL=… uv run pytest tests/test_memory_pg.py   # live; drops that database's tables
just bump patch [dry]                    # edits pyproject.toml + uv.lock only; no commit, no tag
```

Release: bump, commit, `git tag -a vX.Y.Z` and push the tag; CI publishes to PyPI only for `v*`
tags and fails if the tag does not match `pyproject.toml`. The Homebrew formula lives in
`madcat-os/homebrew-tap`: it installs the PyPI sdist with its `uv.lock` and builds psycopg against
Homebrew's libpq (psycopg-binary's bundled dylibs cannot be relocated by Homebrew).

## Architecture

- Identity: `auth.IntrospectionVerifier` is the SDK `TokenVerifier`. It POSTs
  `{issuer}/oauth/introspect` with the shared secret, caches by token SHA-256 (60s active, capped at
  `exp`; 10s rejected), applies `oauth_allowed`, checks the audience only when `aud` is present
  (madcat-web makes `resource` optional), and returns `MadcatToken` (an `AccessToken` subclass with
  email, name, `db_name`, `db_url` as `SecretStr`). The SDK's bearer middleware turns that into
  401/403 and serves RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`; `server.py`
  adds the origin-level well-knowns (resource doc, proxied AS metadata) and `/health`.
- `users.current_user()` reads `get_access_token()` (a contextvar that survives `anyio.to_thread`).
  Without a token it refuses unless `enable_local_user()` was called; only stdio and the CLI call
  it, so an HTTP request can never fall through to the local database.
- `tools/_run.py` is the one path from a tool to data: user → `db.pool.pools().get(db_name, db_url)`
  → `MemoryStore` on a worker thread → one `CallToolResult` text block of sorted, indented JSON, or
  `{"error": …}` with `isError` (returning a list would make the SDK split it into blocks). Tools
  never take identity from their arguments.
- `db/pool.py`: one psycopg pool per `db_name` (max 4 connections, 32 pools LRU; a changed `db_url`
  reopens). Opening runs `db/migrate.py`: numbered `migrations/NNN_name.sql` recorded in
  `schema_migrations` under an advisory lock. 001 is the Rust schema verbatim, so databases the Rust
  server or madcat-web populated are adopted. pgvector must already exist: madcat-web installs it,
  user roles cannot.
- `memory/store.py`: the Rust hybrid recall SQL (pgvector `<=>` + tsvector, RRF k=60) with v2 rules:
  core is permanent, forget deletes edges, classification/relation/confidence/weight validated
  before any database work. `real` columns are read `::float8`.
- `config.Settings`: sources in order init → env (`MADCAT_*`) → regular `.env` → config.toml
  (`[mcp]`, `[local]`, via `TomlSource`); list settings take comma-separated env values. A FIFO
  `.env` (1Password mounts one) is never read: it would block startup.
- stdout is the MCP wire on stdio; human output goes to stderr. stdio exits on SIGINT immediately
  (the stdin reader thread would otherwise keep it alive).
- Tests: `tests/fake_web.py` is a fake madcat-web on `httpx.MockTransport`; `test_http.py` runs the
  ASGI app in-process (lifespan and client in one task). Live database tests need
  `MADCAT_TEST_DB_URL` pointing at a throwaway database with pgvector.

## Rules

- `db_url` and the introspection secret are credentials: `SecretStr`, never logged, never returned
  by a tool (`test_http.py` asserts no response carries the database password). Database errors are
  reported by the server's message only.
- A new tool that touches user data goes through `run` / `run_memory` in `tools/_run.py`.
- Schema changes are new migration files; never edit an applied one.
