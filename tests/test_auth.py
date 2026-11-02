import pytest

from madcat_os.auth import IntrospectionVerifier, MadcatToken, allowed
from tests.fake_web import DB_URL, ISSUER, FakeWeb, oauth_settings


def verifier(web: FakeWeb, **cfg) -> IntrospectionVerifier:
    return IntrospectionVerifier(oauth_settings(**cfg), transport=web.transport)


async def test_active_token_carries_the_user_and_database():
    tok = await verifier(FakeWeb()).verify_token("good")
    assert isinstance(tok, MadcatToken)
    assert (tok.subject, tok.email, tok.name, tok.client_id) == (
        "u-ada",
        "ada@example.com",
        "Ada",
        "client-1",
    )
    assert tok.scopes == ["mcp"] and tok.expires_at == 4_000_000_000
    assert tok.claims == {"iss": ISSUER}
    assert tok.db_name == "swift-ada-01" and tok.db_url.get_secret_value() == DB_URL
    assert "hunter2" not in repr(tok)


async def test_inactive_unknown_and_bad_secret_are_rejected():
    web = FakeWeb()
    assert await verifier(web).verify_token("nope") is None
    assert await verifier(web, oauth_introspect_secret="wrong").verify_token("good") is None


async def test_audience_must_be_this_server_when_present():
    v = verifier(FakeWeb())
    assert await v.verify_token("right-aud") is not None
    assert await v.verify_token("wrong-aud") is None


async def test_allowlist():
    web = FakeWeb()
    assert await verifier(web, oauth_allowed=["ADA@example.com"]).verify_token("good") is not None
    assert await verifier(web, oauth_allowed=["u-ada"]).verify_token("good") is not None
    assert await verifier(web, oauth_allowed=["bob@example.com"]).verify_token("good") is None
    assert await verifier(web, oauth_allowed=[]).verify_token("good") is None
    assert allowed(["*"], "x", None) and not allowed(["a@b"], "x", None)


async def test_answers_are_cached():
    web = FakeWeb()
    v = verifier(web)
    await v.verify_token("good")
    await v.verify_token("good")
    await v.verify_token("nope")
    await v.verify_token("nope")
    assert web.introspections == 2


def test_needs_full_oauth_config():
    with pytest.raises(ValueError):
        IntrospectionVerifier(oauth_settings(oauth_introspect_secret=None))
