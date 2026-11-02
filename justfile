# madcat — python MCP server and CLI

default:
    @just --list

# Bump the version everywhere: just bump <major|minor|patch> [dry]
# Edits files only: no commit, no tag. Every file must match exactly once or
# nothing is written. `dry` prints the plan without touching anything.
bump part dry="":
    #!/usr/bin/env python3
    import pathlib
    import re
    import sys

    part, dry = "{{part}}", "{{dry}}"
    if part not in ("major", "minor", "patch"):
        sys.exit(f"bump: part must be major, minor or patch (got {part!r})")
    if dry not in ("", "dry"):
        sys.exit(f"bump: second argument must be 'dry' or omitted (got {dry!r})")

    root = pathlib.Path(".")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"$', (root / "pyproject.toml").read_text(), re.M)
    if not m:
        sys.exit("bump: no X.Y.Z version found in pyproject.toml")
    major, minor, patch = map(int, m.groups())
    old = f"{major}.{minor}.{patch}"
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"
    o = re.escape(old)

    # (file, pattern). Group 1 and 2 are the text around the version.
    # uv.lock is tracked and CI runs `uv sync --locked`, so it must move with pyproject.
    targets = [
        ("pyproject.toml", rf'^(version = "){o}(")$'),
        ("uv.lock", rf'(name = "madcat-os"\nversion = "){o}(")'),
    ]

    pending = []
    for name, pattern in targets:
        path = root / name
        if not path.exists():
            sys.exit(f"bump: missing {name}")
        out, n = re.subn(pattern, lambda mm: mm.group(1) + new + mm.group(2), path.read_text(), flags=re.M)
        if n != 1:
            sys.exit(f"bump: {name}: expected 1 match for {old}, found {n}; nothing written")
        pending.append((path, out, name))

    print(f"{old} -> {new} ({part}){' [dry run]' if dry else ''}")
    for path, out, name in pending:
        print(f"  update   {name}")
        if not dry:
            path.write_text(out)
    if not dry:
        print(f"\nNext: commit, then: git tag -a v{new} -m 'madcat-os {new}' && git push origin main v{new}")

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

# Serve over HTTP on 127.0.0.1:4097 (needs MADCAT_PUBLIC_URL, MADCAT_OAUTH_ISSUER, MADCAT_OAUTH_INTROSPECT_SECRET)
http:
    uv run madcat mcp serve --transport http

# Install as a uv tool (puts madcat on PATH)
install:
    uv tool install --force --editable .
