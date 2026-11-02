import os
import subprocess
import sys

import pytest

from madcat_os.config import Settings, embedding_config
from tests.conftest import write_toml


def test_sources_in_order_env_dotenv_toml(tmp_path, monkeypatch):
    write_toml(
        '[mcp]\nport = 5000\nhost = "0.0.0.0"\noauth_allowed = ["*"]\n[local]\ndb_url = "postgres://l/x"\n'
    )
    (tmp_path / ".env").write_text("MADCAT_PORT=6000\n")
    s = Settings()
    assert (s.port, s.host, s.oauth_allowed) == (6000, "0.0.0.0", ["*"])
    assert s.local_db_url.get_secret_value() == "postgres://l/x"
    monkeypatch.setenv("MADCAT_PORT", "7000")
    monkeypatch.setenv("MADCAT_OAUTH_ALLOWED", "a@x.com, B@y.com")
    s = Settings()
    assert (s.port, s.oauth_allowed) == (7000, ["a@x.com", "B@y.com"])
    assert Settings(port=1).port == 1


def test_oauth_configured_and_resource_url():
    assert not Settings().oauth_configured
    s = Settings(public_url="https://m.test/", oauth_issuer="https://w.test", oauth_introspect_secret="x")
    assert s.oauth_configured and s.resource_url == "https://m.test/mcp"


def test_embedding_config(monkeypatch):
    write_toml('[embedding]\nprovider = "openai"\nurl = "http://e#x"\nmodel = "m"\n')
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    cfg = embedding_config()
    assert (cfg.provider, cfg.url, cfg.model, cfg.api_key) == ("openai", "http://e#x", "m", "sk")
    monkeypatch.setenv("MADCAT_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("MADCAT_EMBED_URL", "http://env:11434")
    monkeypatch.setenv("MADCAT_EMBED_MODEL", "bge-m3")
    monkeypatch.setenv("MADCAT_EMBED_API_KEY", "ek")
    cfg = embedding_config()
    assert (cfg.provider, cfg.url, cfg.model, cfg.api_key) == ("ollama", "http://env:11434", "bge-m3", "ek")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_fifo_dotenv_does_not_block_startup(tmp_path):
    # A named pipe .env (1Password mounts one in ~) would block the read and hang the server.
    os.mkfifo(tmp_path / ".env")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MADCAT_")}
    cmd = [sys.executable, "-c", "from madcat_os.config import Settings; print(Settings().port)"]
    r = subprocess.run(cmd, cwd=tmp_path, env=env, timeout=20, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "4097"
