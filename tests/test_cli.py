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


def test_list_omits_removed_tools():
    r = runner.invoke(app, ["mcp", "list"])
    assert all(name not in r.stdout for name in ("ping", "server_info", "memory_list"))


def test_call_whoami_runs_as_local():
    r = runner.invoke(app, ["mcp", "call", "whoami"])
    assert r.exit_code == 0 and '"local"' in r.stdout


def test_memory_without_local_database_fails():
    r = runner.invoke(app, ["mcp", "call", "memory_get", '{"ids": []}'])
    assert r.exit_code == 1 and "MADCAT_LOCAL_DB_URL" in r.stdout


def test_bad_json_and_http_without_oauth():
    assert runner.invoke(app, ["mcp", "call", "whoami", "{nope"]).exit_code == 2
    r = runner.invoke(app, ["mcp", "serve", "--transport", "http"])
    assert r.exit_code == 2 and "HTTP needs OAuth" in r.stderr
