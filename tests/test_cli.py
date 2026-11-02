from typer.testing import CliRunner

from madcat_os import __version__
from madcat_os.cli import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and __version__ in r.stdout


def test_list():
    r = runner.invoke(app, ["mcp", "list"])
    assert r.exit_code == 0
    for name in ("whoami", "memory_recall", "memory_graph"):
        assert name in r.stdout


def test_list_has_no_ping_or_server_info():
    r = runner.invoke(app, ["mcp", "list"])
    assert "ping" not in r.stdout and "server_info" not in r.stdout


def test_call_whoami_runs_as_local():
    r = runner.invoke(app, ["mcp", "call", "whoami"])
    assert r.exit_code == 0 and '"local"' in r.stdout


def test_memory_without_local_database_fails():
    r = runner.invoke(app, ["mcp", "call", "memory_list"])
    assert r.exit_code == 1 and "MADCAT_LOCAL_DB_URL" in r.stdout


def test_bad_json_and_http_without_oauth():
    assert runner.invoke(app, ["mcp", "call", "whoami", "{nope"]).exit_code == 2
    r = runner.invoke(app, ["mcp", "serve", "--transport", "http"])
    assert r.exit_code == 2 and "HTTP needs OAuth" in r.stderr
