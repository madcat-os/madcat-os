from typer.testing import CliRunner

from madcat_os import __version__
from madcat_os.cli import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.output


def test_list_has_ping():
    r = runner.invoke(app, ["mcp", "list"])
    assert r.exit_code == 0
    assert "ping" in r.output


def test_call_ping():
    r = runner.invoke(app, ["mcp", "call", "ping"])
    assert r.exit_code == 0
    assert "pong" in r.output


def test_call_bad_json():
    r = runner.invoke(app, ["mcp", "call", "ping", "{nope"])
    assert r.exit_code == 2
