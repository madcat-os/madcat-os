"""Connection pools, one per user database.

Pools are keyed by the user's ``db_name`` and opened on first use, which also runs migrations.
At most ``MAX_OPEN`` pools stay open; the least recently used one is closed when a new user
arrives (connections it has lent out close when they come back). A user whose ``db_url`` changed
(madcat-web rotated the role's password) gets a fresh pool.
"""

from __future__ import annotations

import atexit
import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from madcat_os.db.migrate import migrate

MAX_OPEN = 32
MAX_SIZE = 4
OPEN_TIMEOUT = 10.0


class DatabaseError(Exception):
    pass


@dataclass
class _Entry:
    pool: ConnectionPool
    url_digest: str


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class Pools:
    def __init__(self, max_open: int = MAX_OPEN, max_size: int = MAX_SIZE) -> None:
        self.max_open = max_open
        self.max_size = max_size
        self._pools: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._opening: dict[str, threading.Lock] = {}

    def get(self, db_name: str, db_url: str) -> ConnectionPool:
        digest = _digest(db_url)
        if pool := self._cached(db_name, digest):
            return pool
        with self._lock:
            opening = self._opening.setdefault(db_name, threading.Lock())
        with opening:
            # Another thread may have opened it while this one waited.
            if pool := self._cached(db_name, digest):
                return pool
            pool = self._open(db_url)
            with self._lock:
                stale = self._pools.pop(db_name, None)
                self._pools[db_name] = _Entry(pool, digest)
                evicted = []
                while len(self._pools) > self.max_open:
                    evicted.append(self._pools.popitem(last=False)[1])
            for entry in [stale, *evicted]:
                if entry:
                    entry.pool.close()
            return pool

    def _cached(self, db_name: str, digest: str) -> ConnectionPool | None:
        with self._lock:
            entry = self._pools.get(db_name)
            if entry and entry.url_digest == digest:
                self._pools.move_to_end(db_name)
                return entry.pool
        return None

    def _open(self, db_url: str) -> ConnectionPool:
        pool = ConnectionPool(
            db_url, min_size=0, max_size=self.max_size, kwargs={"row_factory": dict_row}, open=False
        )
        try:
            pool.open(wait=False)
            with pool.connection(timeout=OPEN_TIMEOUT) as conn:
                migrate(conn)
        except psycopg.Error as e:
            pool.close()
            # Report the server's message only; never the connection string.
            detail = e.diag.message_primary or str(e)
            raise DatabaseError(f"cannot open user database: {detail}") from None
        except Exception:
            pool.close()
            raise
        return pool

    def open_names(self) -> list[str]:
        with self._lock:
            return list(self._pools)

    def close(self) -> None:
        with self._lock:
            entries = list(self._pools.values())
            self._pools.clear()
        for entry in entries:
            entry.pool.close()


_pools: Pools | None = None
_pools_lock = threading.Lock()


def pools() -> Pools:
    global _pools
    with _pools_lock:
        if _pools is None:
            _pools = Pools()
            atexit.register(_pools.close)
        return _pools
