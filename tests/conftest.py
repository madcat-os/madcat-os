import os

import pytest

from madcat_os import config, users


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real config.toml, no MADCAT_* environment, local user off, fresh settings."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("MADCAT_") and name != "MADCAT_TEST_DB_URL":
            monkeypatch.delenv(name)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(users, "_local_enabled", False)
    config.madcat_toml.cache_clear()
    config.settings.cache_clear()
    yield
    config.madcat_toml.cache_clear()
    config.settings.cache_clear()


def write_toml(text: str) -> None:
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    config.madcat_toml.cache_clear()
    config.settings.cache_clear()
