import pytest

from madcat_os import users
from madcat_os.db.pool import Pools
from madcat_os.users import User, UserError, current_user, enable_local_user
from tests.conftest import write_toml


def test_no_token_is_refused_unless_local_mode_is_on():
    with pytest.raises(UserError, match="not authenticated"):
        current_user()
    enable_local_user()
    user = current_user()
    assert user.local and user.id == "local"
    with pytest.raises(UserError, match="MADCAT_LOCAL_DB_URL"):
        user.database()


def test_local_user_database_from_config():
    write_toml('[local]\ndb_url = "postgres://me@h/mine"\n')
    users.enable_local_user()
    assert current_user().database() == ("local", "postgres://me@h/mine")


def test_public_view_has_no_database_url():
    user = User("u", "e@x", "E", "db", users.SecretStr("postgres://u:pw@h/db"))
    assert "pw" not in repr(user.public()) and "db_url" not in user.public()


class FakePool:
    def __init__(self, url):
        self.url, self.closed = url, False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pools(monkeypatch):
    monkeypatch.setattr(Pools, "_open", lambda self, url: FakePool(url))
    return Pools(max_open=2)


def test_one_pool_per_user_database(fake_pools):
    a = fake_pools.get("ada", "postgres://ada")
    assert fake_pools.get("ada", "postgres://ada") is a
    assert fake_pools.get("bob", "postgres://bob") is not a


def test_least_recently_used_pool_is_closed(fake_pools):
    a = fake_pools.get("ada", "u1")
    b = fake_pools.get("bob", "u2")
    fake_pools.get("ada", "u1")
    fake_pools.get("cy", "u3")
    assert b.closed and not a.closed
    assert fake_pools.open_names() == ["ada", "cy"]


def test_changed_database_url_reopens_the_pool(fake_pools):
    old = fake_pools.get("ada", "postgres://ada:old@h/x")
    new = fake_pools.get("ada", "postgres://ada:new@h/x")
    assert new is not old and old.closed and new.url.endswith("new@h/x")
