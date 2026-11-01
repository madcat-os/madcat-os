# madcat — python MCP server and CLI

default:
    @just --list

# Install the environment (runtime + dev)
sync:
    uv sync

# Lint and format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix lint and format
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Tests
test:
    uv run pytest -q

# Everything that must pass before a push
check: lint test
    uv run madcat mcp list >/dev/null

# Tool inventory
tools:
    uv run madcat mcp list

# Call one tool locally: just call ping '{}'
call name args='{}':
    uv run madcat mcp call {{name}} '{{args}}'

# Serve over stdio
stdio:
    uv run madcat mcp serve

# Serve over HTTP on 127.0.0.1:4097 (set MADCAT_MCP_TOKEN)
http:
    uv run madcat mcp serve --transport http

# Install as a uv tool (puts madcat on PATH)
install:
    uv tool install --force --editable .
