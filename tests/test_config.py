import os
import subprocess
import sys

import pytest

from madcat_os.config import Settings

LOAD = "from madcat_os.config import Settings; print(Settings().port)"


def test_regular_dotenv_is_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MADCAT_MCP_PORT=4321\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MADCAT_MCP_PORT", raising=False)
    assert Settings().port == 4321
    assert Settings(port=1).port == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_fifo_dotenv_does_not_block_startup(tmp_path):
    # A named pipe .env (1Password mounts one in ~) used to hang the server before the handshake.
    os.mkfifo(tmp_path / ".env")
    env = {k: v for k, v in os.environ.items() if k != "MADCAT_MCP_PORT"}
    cmd = [sys.executable, "-c", LOAD]
    r = subprocess.run(cmd, cwd=tmp_path, env=env, timeout=20, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "4097"
