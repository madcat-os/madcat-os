"""The user a tool call runs as.

Over HTTP every call carries a madcat-web OAuth token; the SDK's bearer middleware verifies it and
``get_access_token()`` returns it (also inside worker threads: the SDK copies the request context).
Over stdio and from ``madcat mcp call`` there is no token: once ``enable_local_user()`` has been
called, calls run as the single local user whose database is ``local_db_url``. The HTTP server
never enables it, so a request without a token can never fall through to the local database.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from pydantic import SecretStr

from madcat_os.auth import MadcatToken
from madcat_os.config import settings

LOCAL_NAME = "local"

_local_enabled = False


class UserError(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    email: str | None
    name: str | None
    db_name: str | None
    db_url: SecretStr | None
    local: bool = False

    def public(self) -> dict:
        """What a tool may show about the user: never the database URL."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "db_name": self.db_name,
            "local": self.local,
        }

    def database(self) -> tuple[str, str]:
        """(db_name, db_url) of this user's database, or a UserError saying why there is none."""
        if not self.db_name or not self.db_url:
            raise UserError(
                "no database provisioned for this account"
                if not self.local
                else "no local database: set MADCAT_LOCAL_DB_URL or [local].db_url"
            )
        return self.db_name, self.db_url.get_secret_value()


def enable_local_user() -> None:
    """Let token-less calls run as the local user. Only stdio and the CLI call this."""
    global _local_enabled
    _local_enabled = True


def current_user() -> User:
    token = get_access_token()
    if isinstance(token, MadcatToken):
        return User(
            id=token.subject or "",
            email=token.email,
            name=token.name,
            db_name=token.db_name,
            db_url=token.db_url,
        )
    if token is not None:
        raise UserError("unexpected access token type")
    if not _local_enabled:
        raise UserError("not authenticated")
    url = settings().local_db_url
    return User(id=LOCAL_NAME, email=None, name=None, db_name=LOCAL_NAME, db_url=url, local=True)
