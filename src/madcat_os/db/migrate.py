"""Versioned schema migrations, applied to each user database on first use.

Migrations are ``migrations/NNN_name.sql`` files, applied in order and recorded in
``schema_migrations``. 001 is the Rust madcat schema (all ``IF NOT EXISTS``), so databases the Rust
server or madcat-web already populated are adopted as they are. The whole run holds an advisory
lock, so two server processes opening the same database do not race.
"""

from __future__ import annotations

import re
import time
from importlib.resources import files

import psycopg
from psycopg.rows import tuple_row

LOCK_KEY = 0x6D616463  # "madc"


class MigrationError(Exception):
    pass


def migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every bundled migration, in version order."""
    out = []
    for entry in files("madcat_os.db").joinpath("migrations").iterdir():
        m = re.fullmatch(r"(\d+)_(\w+)\.sql", entry.name)
        if m:
            out.append((int(m.group(1)), m.group(2), entry.read_text()))
    return sorted(out)


def migrate(conn: psycopg.Connection) -> list[int]:
    """Apply pending migrations; returns the versions applied. pgvector must already be installed
    (madcat-web creates it in every user database; a user role cannot)."""
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        if not cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
            db = cur.execute("SELECT current_database()").fetchone()
            raise MigrationError(
                f"pgvector is not installed in database {db[0] if db else '?'}; "
                "as a superuser run `CREATE EXTENSION vector;` there"
            )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version integer PRIMARY KEY, name text NOT NULL, applied_at bigint NOT NULL)"
        )
        done = {row[0] for row in cur.execute("SELECT version FROM schema_migrations").fetchall()}
        applied = []
        for version, name, sql in migrations():
            if version in done:
                continue
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s)",
                (version, name, time.time_ns() // 1_000_000),
            )
            applied.append(version)
        return applied
