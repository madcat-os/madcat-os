"""A fake madcat-web: introspection and authorization-server metadata over httpx.MockTransport."""

import httpx

from madcat_os.config import Settings

ISSUER = "https://web.test"
PUBLIC = "https://mcp.test"
SECRET = "s3cret"
DB_URL = "postgres://ada_role:hunter2@db.test:5432/ada_eems"


def active(**over):
    claims = {
        "active": True,
        "sub": "u-ada",
        "username": "ada@example.com",
        "email": "ada@example.com",
        "name": "Ada",
        "client_id": "client-1",
        "scope": "mcp",
        "token_type": "Bearer",
        "iat": 1_700_000_000,
        "exp": 4_000_000_000,
        "iss": ISSUER,
        "db_name": "swift-ada-01",
        "db_url": DB_URL,
    }
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


TOKENS = {
    "good": active(),
    "other-user": active(
        sub="u-bob", email="bob@example.com", username="bob@example.com", db_name="bold-bob-02"
    ),
    "no-scope": active(scope=""),
    "right-aud": active(aud=f"{PUBLIC}/mcp"),
    "wrong-aud": active(aud="https://elsewhere.test/mcp"),
    "no-db": active(db_name=None, db_url=None),
}


class FakeWeb:
    def __init__(self, tokens=None):
        self.tokens = TOKENS if tokens is None else tokens
        self.introspections = 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": ISSUER, "scopes_supported": ["mcp"]})
        if request.url.path == "/oauth/introspect" and request.method == "POST":
            if request.headers.get("authorization") != f"Bearer {SECRET}":
                return httpx.Response(401, json={"error": "invalid_client"})
            self.introspections += 1
            form = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json=self.tokens.get(form.get("token", ""), {"active": False}))
        return httpx.Response(404)


def oauth_settings(**over) -> Settings:
    values = dict(
        public_url=PUBLIC,
        oauth_issuer=ISSUER,
        oauth_introspect_secret=SECRET,
        oauth_allowed=["*"],
        host="127.0.0.1",
        port=4097,
    )
    values.update(over)
    return Settings(**values)
