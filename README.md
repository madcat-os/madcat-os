# madcat-os (binary: `madcat`)

MADCAT MCP server: OAuth-first, every user on their own database.

- Over **HTTP** every call carries a [madcat-web](https://madcat-os.com) OAuth access token. The
  server checks it with madcat-web (token introspection) and runs the call against that user's own
  Postgres + pgvector database.
- Over **stdio**, and with `madcat mcp call`, calls run as one local user configured on the machine.

Tools: `whoami` and EEMS memory: `memory_store`, `memory_recall`,
`memory_get`, `memory_forget`, `memory_classify`, `memory_list`, `memory_link`, `memory_graph`.

## Quick start

```bash
uv sync
uv run madcat status
uv run madcat mcp list
MADCAT_LOCAL_DB_URL=postgres://me@localhost/my_eems uv run madcat mcp call memory_list
```

## HTTP with OAuth

madcat-web is the authorization server; this server is the resource server. Configure:

| Environment | `config.toml` | |
|---|---|---|
| `MADCAT_PUBLIC_URL` | `[mcp] public_url` | Public origin, e.g. `https://mcp.madcat-os.com`; the MCP endpoint is `{public_url}/mcp` |
| `MADCAT_OAUTH_ISSUER` | `[mcp] oauth_issuer` | madcat-web origin, e.g. `https://madcat-os.com` |
| `MADCAT_OAUTH_INTROSPECT_SECRET` | `[mcp] oauth_introspect_secret` | madcat-web's `OAUTH_INTROSPECT_SECRET` |
| `MADCAT_OAUTH_ALLOWED` | `[mcp] oauth_allowed` | Emails or user ids let in (comma-separated), or `*` |
| `MADCAT_HOST`, `MADCAT_PORT` | `[mcp] host`, `port` | Bind address (default `127.0.0.1:4097`) |

```bash
uv run madcat mcp serve --transport http
```

- `/mcp` without a valid token answers 401 with `WWW-Authenticate: Bearer … resource_metadata=…`,
  and a token without the `mcp` scope answers 403.
- Discovery: `/.well-known/oauth-protected-resource[/mcp]` (RFC 9728) and
  `/.well-known/oauth-authorization-server[/mcp]` (madcat-web's metadata, proxied).
- `/health` is open.

Each user's database is the `db_url` madcat-web returns from introspection: a Postgres role and an
`{ident}_eems` database it provisions with pgvector installed. This server never creates databases
or roles. On first use it applies its migrations there (`schema_migrations`), adopting tables the
Rust madcat created. An account without a provisioned database gets a tool error.

Add it in Claude as a custom connector with the URL `{public_url}/mcp`; Claude runs the OAuth flow
with madcat-web.

## Local mode (stdio and CLI)

Set `MADCAT_LOCAL_DB_URL` (or `[local] db_url`) to your own database. Claude Code over stdio:

```json
{ "mcpServers": { "madcat": { "command": "madcat", "args": ["mcp", "serve"] } } }
```

## Memory rules

- `memory_store` deduplicates on namespace, subject and content.
- `memory_recall` fuses vector similarity and full-text rank.
- `core` memories are permanent: they cannot be reclassified or forgotten.
- `memory_forget` also removes the memory's links.
- `memory_link` takes `related_to`, `depends_on`, `contradicts`, `extends` or `supersedes`, between
  two existing memories.

Embeddings come from `[embedding]` in `config.toml`: `provider` (`ollama` or `openai`), `url`
(required, or `MADCAT_EMBED_URL`), `model`, `api_key` (falls back to `OPENAI_API_KEY`).

## Development

```bash
just check                                          # ruff, format check, pytest, tool list smoke test
MADCAT_TEST_DB_URL=postgres://…/throwaway uv run pytest tests/test_memory_pg.py   # drops that db's tables
```

## Layout

```
src/madcat_os/
  config.py     Settings: env, regular .env, ~/.config/madcat/config.toml
  auth.py       madcat-web token introspection (cached) → MadcatToken
  users.py      current_user(): the token's user, or the local user on stdio/CLI
  db/           per-user connection pools and versioned migrations
  memory/       embedders and the memory store
  tools/        tool modules; _run.py resolves the caller's store and renders results
  server.py     MCPServer with OAuth for HTTP, discovery routes, stdio
  cli.py        status, mcp list | call | serve
```
