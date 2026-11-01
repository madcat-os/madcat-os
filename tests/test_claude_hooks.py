import json
import tomllib

import pytest
from typer.testing import CliRunner

from madcat_os import config
from madcat_os.claude_hooks import (
    WELCOME_CAP,
    Persona,
    compact_reminder,
    is_madcat_tool,
    split_mcp_tool,
    welcome_prompt,
)
from madcat_os.cli import app

runner = CliRunner()

SAMPLE_CART = """
tts_enabled = true
tts_session_lock = "~/.local/share/madcat/tts-session.lock"

[persona]
name = "BT-7274"
tagline = "Vanguard-class Titan AI"
default_voice = "bt7274-en-piper"
prompt = "You are BT-7274."

[persona.operator]
name = "Adam"
callsign = "Pilot"

[persona.behavior]
tone = "calm, precise"

[[voices]]
id = "bt7274-en-piper"
"""


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Isolated HOME with no config.toml, no carts and no hook env overrides."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (
        "MADCAT_CLAUDE_STATE_DIR",
        "MADCAT_CLAUDE_MCP_SERVER",
        "MADCAT_CLAUDE_WELCOME",
        "CLAUDE_PLUGIN_DATA",
    ):
        monkeypatch.delenv(name, raising=False)
    config.madcat_toml.cache_clear()
    yield tmp_path
    config.madcat_toml.cache_clear()


def write_cart(home, tag, text, active=True):
    carts = home / ".config" / "madcat" / "carts"
    carts.mkdir(parents=True, exist_ok=True)
    (carts / f"{tag}.toml").write_text(text)
    if active:
        state = home / ".local" / "share" / "madcat"
        state.mkdir(parents=True, exist_ok=True)
        (state / "tts-state.toml").write_text(f'active_cart = "{tag}"\n')


def test_persona_comes_from_the_cart():
    p = Persona.from_cart("bt7274", tomllib.loads(SAMPLE_CART))
    assert p.name == "BT-7274"
    assert p.tts_enabled
    assert p.default_voice == "bt7274-en-piper"
    assert [k for k, _ in p.sections] == ["operator", "behavior"]
    text = welcome_prompt("startup", p, ["JARVIS (`jarvis`)"])
    assert text.startswith("# MADCAT boot (session source: startup)\n")
    assert "You are **BT-7274** — Vanguard-class Titan AI." in text
    assert "**Operator**\n- callsign: Pilot\n- name: Adam\n" in text
    assert "one `memory_recall` call for operator preferences (subject user.preference, limit 3)" in text
    assert "core.self-model" not in text and "cart.bt7274" not in text
    assert "this cart has TTS enabled" in text
    assert "JARVIS (`jarvis`)" in text
    assert "Then greet the operator" in text
    assert len(text) < WELCOME_CAP


def test_cart_welcome_replaces_the_greeting_rule():
    doc = tomllib.loads(SAMPLE_CART)
    doc["welcome"] = "Report in, Titan."
    text = welcome_prompt("resume", Persona.from_cart("bt7274", doc), [])
    assert "Report in, Titan." in text
    assert "Then greet the operator" not in text


def test_missing_cart_falls_back_to_the_generic_prompt():
    text = welcome_prompt("startup", None, [])
    assert "No persona cart is loaded" in text
    assert "subject user.preference" in text and "cart.<cart-id>" not in text
    assert "the active persona cart, its voice," in compact_reminder(None)
    p = Persona.from_cart("ghost", {})
    assert p.name == "ghost"
    assert not p.tts_enabled
    assert "`ghost` (ghost)" in compact_reminder(p)


def test_splits_server_and_tool():
    assert split_mcp_tool("mcp__madcat__tts_speak") == ("madcat", "tts_speak")
    assert split_mcp_tool("mcp__e87ba275-eab5-4922-a18d-bedc77975e46__memory_recall") == (
        "e87ba275-eab5-4922-a18d-bedc77975e46",
        "memory_recall",
    )
    assert split_mcp_tool("Bash") is None
    assert split_mcp_tool("mcp__x") is None


def test_recognises_madcat_tools_by_name_or_server(monkeypatch):
    assert is_madcat_tool("mcp__anything__server_info")
    assert not is_madcat_tool("mcp__other__foo")
    assert is_madcat_tool("mcp__other__foo", "other")
    assert not is_madcat_tool("Bash", "madcat")
    monkeypatch.setenv("MADCAT_CLAUDE_MCP_SERVER", "other")
    assert is_madcat_tool("mcp__other__foo")


def pre_tool(event, *extra):
    argv = ["claude", "hooks", "pre-tool", "inject_session_id", *extra]
    r = runner.invoke(app, argv, input=json.dumps(event))
    assert r.exit_code == 0, r.output
    return r.stdout


def test_inject_session_id_adds_the_arg_and_records_the_session(home):
    out = pre_tool(
        {
            "session_id": "s1",
            "cwd": "/w",
            "tool_name": "mcp__e87b__ping",
            "tool_input": {"x": 1},
        }
    )
    assert json.loads(out) == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"x": 1, "session_id": "s1"}}
    }
    assert (home / ".claude" / "current-session-id").read_text() == "s1\n"
    state = json.loads((home / ".claude" / "current-session.json").read_text())
    assert state["session_id"] == "s1" and state["cwd"] == "/w" and state["transcript_path"] is None


def test_inject_session_id_leaves_other_calls_alone(home, monkeypatch):
    assert pre_tool({"session_id": "s1", "tool_name": "Bash", "tool_input": {}}) == ""
    already = {"session_id": "s0"}
    assert pre_tool({"session_id": "s1", "tool_name": "mcp__x__ping", "tool_input": already}) == ""
    assert pre_tool({"session_id": "s1", "tool_name": "mcp__x__foo"}) == ""
    assert '"updatedInput":{"session_id":"s1"}' in pre_tool(
        {"session_id": "s1", "tool_name": "mcp__x__foo"}, "--server", "x"
    )
    monkeypatch.setenv("MADCAT_CLAUDE_STATE_DIR", str(home / "state"))
    assert pre_tool({"tool_name": "mcp__x__ping"}) == ""
    assert not (home / "state").exists()
    assert pre_tool({"session_id": "s2"}) == ""
    assert (home / "state" / "current-session-id").read_text() == "s2\n"


def test_hooks_never_fail_on_bad_input():
    for argv in (["pre-tool", "inject_session_id"], ["session-start", "welcome"], ["pre-tool", "nope"]):
        r = runner.invoke(app, ["claude", "hooks", *argv], input="{not json")
        assert r.exit_code == 0


def session_start(event, *extra):
    argv = ["claude", "hooks", "session-start", "welcome", *extra]
    r = runner.invoke(app, argv, input=json.dumps(event))
    assert r.exit_code == 0, r.output
    return r.stdout


def test_welcome_uses_the_active_cart_and_roster(home):
    write_cart(home, "bt7274", SAMPLE_CART)
    write_cart(home, "jarvis", '[persona]\nname = "JARVIS"\n', active=False)
    write_cart(home, "bare", "tts_enabled = false\n", active=False)
    out = session_start({"source": "startup"})
    assert out.startswith("# MADCAT boot (session source: startup)\n")
    assert "The active persona cart is `bt7274`." in out
    assert "persona name (JARVIS (`jarvis`))," in out
    assert "compacted" in session_start({"source": "compact"})
    assert "`jarvis` (JARVIS)" in session_start({"source": "compact"}, "--cart", "jarvis")


def test_welcome_override_file_wins(home, monkeypatch):
    write_cart(home, "bt7274", SAMPLE_CART)
    (home / "welcome.md").write_text("custom boot")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(home))
    assert session_start({"source": "startup"}) == "custom boot\n"
    assert "compacted" in session_start({"source": "compact"})


def test_welcome_without_a_cart():
    assert "No persona cart is loaded" in session_start({})
