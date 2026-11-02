"""OAuth 2.1 resource-server side: validate madcat-web access tokens by introspection.

madcat-web is the authorization server (sign-in, consent, client registration, tokens). This
server only checks the bearer token on each request with ``POST {issuer}/oauth/introspect``
(RFC 7662, authenticated with the shared introspection secret) and caches the answer briefly. An
active token names the user, and its ``db_name`` / ``db_url`` name that user's own database.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

import httpx
from mcp.server.auth.provider import AccessToken
from pydantic import SecretStr

from madcat_os.config import Settings

log = logging.getLogger("madcat_os.auth")

POSITIVE_TTL = 60.0
NEGATIVE_TTL = 10.0
CACHE_MAX = 4096
TIMEOUT = httpx.Timeout(10.0)


class MadcatToken(AccessToken):
    """An introspected madcat-web access token and the user it belongs to."""

    email: str | None = None
    name: str | None = None
    db_name: str | None = None
    db_url: SecretStr | None = None


def allowed(allow: list[str], subject: str, email: str | None) -> bool:
    """``*`` admits everyone; otherwise an entry matches the email (any case) or the exact subject."""
    for entry in allow:
        if entry == "*" or entry == subject or (email and entry.lower() == email.lower()):
            return True
    return False


class IntrospectionVerifier:
    """``TokenVerifier`` for the MCP SDK: madcat-web introspection with a small TTL cache.

    Both answers are cached by the token's SHA-256: an active token for 60s (never past its
    ``exp``), a rejected one for 10s. The cache is cleared when it outgrows 4096 entries.
    """

    def __init__(self, cfg: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not cfg.oauth_configured:
            raise ValueError("OAuth needs public_url, oauth_issuer and oauth_introspect_secret")
        self.issuer = (cfg.oauth_issuer or "").rstrip("/")
        self.resource = cfg.resource_url
        self.public_url = (cfg.public_url or "").rstrip("/")
        self.allow = cfg.oauth_allowed
        secret = cfg.oauth_introspect_secret.get_secret_value() if cfg.oauth_introspect_secret else ""
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT, transport=transport, headers={"Authorization": f"Bearer {secret}"}
        )
        self._cache: dict[str, tuple[float, MadcatToken | None]] = {}
        self._lock = threading.Lock()
        if not self.allow:
            log.warning("oauth_allowed is empty: every token will be refused")

    async def verify_token(self, token: str) -> MadcatToken | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        result = await self._introspect(token)
        if result is None:
            expires = now + NEGATIVE_TTL
        else:
            expires = now + POSITIVE_TTL
            if result.expires_at:
                expires = min(expires, result.expires_at)
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (expires, result)
        return result

    async def _introspect(self, token: str) -> MadcatToken | None:
        try:
            r = await self._client.post(f"{self.issuer}/oauth/introspect", data={"token": token})
        except httpx.HTTPError as e:
            log.warning("introspection request failed: %s", e)
            return None
        if r.status_code != 200:
            log.warning("introspection refused: HTTP %s", r.status_code)
            return None
        try:
            v = r.json()
        except ValueError:
            log.warning("introspection answered with invalid JSON")
            return None
        if not isinstance(v, dict) or v.get("active") is not True:
            return None
        return self._token(token, v)

    def _token(self, token: str, v: dict) -> MadcatToken | None:
        subject = str(v.get("sub") or "")
        email = v.get("email") or v.get("username")
        if not subject:
            log.warning("introspection: active token without sub")
            return None
        if not allowed(self.allow, subject, email):
            log.warning("token refused: %s is not in oauth_allowed", email or subject)
            return None
        aud = v.get("aud")
        audiences = aud if isinstance(aud, list) else [aud] if aud else []
        if audiences and not {a.rstrip("/") for a in audiences} & {self.resource, self.public_url}:
            log.warning("token refused: audience %s is not this server", audiences)
            return None
        exp = v.get("exp")
        return MadcatToken(
            token=token,
            client_id=str(v.get("client_id") or ""),
            scopes=str(v.get("scope") or "").split(),
            expires_at=int(exp) if isinstance(exp, int | float) else None,
            resource=self.resource,
            subject=subject,
            claims={"iss": v.get("iss") or self.issuer},
            email=email,
            name=v.get("name"),
            db_name=v.get("db_name") or None,
            db_url=SecretStr(v["db_url"]) if v.get("db_url") else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
