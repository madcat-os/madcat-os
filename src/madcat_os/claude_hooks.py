"""Claude Code hook handlers: ``madcat claude hooks pre-tool <name>`` and
``madcat claude hooks session-start <name>``.

Claude Code runs the configured command with the event JSON on stdin and uses what it prints to
stdout: a PreToolUse hook's JSON is applied to the tool call, a SessionStart hook's plain text goes
into Claude's context before the first prompt (capped at 10,000 characters). The contract for
everything here: read stdin, print only the hook's own output, exit 0. No network, no database; a
hook that fails must never block a tool call or a session. Diagnostics go to stderr.

``pre-tool inject_session_id``: MCP gives a server no way to know which Claude Code session is
calling, so the hook records the session (``<state_dir>/current-session-id``,
``<state_dir>/current-session.json``) and, for madcat tools, adds ``session_id`` to the call's
arguments.

``session-start welcome``: prints the MADCAT boot prompt built from the active persona cart
(``tts-state.toml`` → ``carts/<tag>.toml``): persona, operator, behaviour, TTS lock, the boot
sequence and the greeting rule. After compaction it prints a short re-anchor reminder instead. An
operator file (``config.claude_welcome_override``) replaces the built-in prompt wholesale.

Ported from the Rust madcat ``src/claude_hooks.rs``; keep the prompt text in step with it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from madcat_os import cart, config

SESSION_ARG = "session_id"

# Claude Code drops SessionStart stdout beyond this many characters.
WELCOME_CAP = 10_000

# Render order for the persona sub-tables; anything else follows alphabetically.
SECTION_ORDER = ("operator", "behavior", "protocols", "constraints")


def _warn(msg: str) -> None:
    print(f"madcat hook: {msg}", file=sys.stderr)


def _read_stdin_json() -> Any:
    try:
        return json.loads(sys.stdin.read())
    except (OSError, ValueError):
        return None


def _str_field(v: Any, key: str) -> str | None:
    s = v.get(key) if isinstance(v, dict) else None
    return s.strip() or None if isinstance(s, str) else None


def _dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# PreToolUse: inject_session_id
# ---------------------------------------------------------------------------


def pre_tool(name: str, server: str | None = None) -> None:
    """Entry point for ``madcat claude hooks pre-tool <name>``."""
    if name != "inject_session_id":
        print(
            f"madcat claude hooks pre-tool: unknown hook `{name}` (known: inject_session_id)",
            file=sys.stderr,
        )
        return
    try:
        inject_session_id(server)
    except Exception as e:  # noqa: BLE001 — a hook must never fail the tool call
        _warn(f"inject_session_id: {e}")


def split_mcp_tool(name: str) -> tuple[str, str] | None:
    """Split ``mcp__<server>__<tool>`` into its parts."""
    if not name.startswith("mcp__"):
        return None
    server, sep, tool = name.removeprefix("mcp__").partition("__")
    return (server, tool) if sep and server and tool else None


def _tool_names() -> set[str]:
    # Imported here: the MCP SDK is the slow part of startup, and most hook calls never get this far.
    import asyncio

    from madcat_os.server import build_server

    return {t.name for t in asyncio.run(build_server().list_tools())}


def is_madcat_tool(tool_name: str, server_override: str | None = None) -> bool:
    """Is ``tool_name`` one of ours? Either the server part matches the configured/explicit server
    name, or the tool part names a registered madcat tool."""
    parts = split_mcp_tool(tool_name)
    if parts is None:
        return False
    server, tool = parts
    if server in (server_override, config.claude_mcp_server_name()):
        return True
    return tool in _tool_names()


def record_session(event: dict, sid: str) -> None:
    state_dir = config.claude_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _warn(f"cannot create {state_dir}: {e}")
        return
    try:
        (state_dir / "current-session-id").write_text(f"{sid}\n")
    except OSError as e:
        _warn(f"write current-session-id: {e}")
    state = {
        "session_id": sid,
        "cwd": event.get("cwd"),
        "transcript_path": event.get("transcript_path"),
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        (state_dir / "current-session.json").write_text(f"{_dumps(state)}\n")
    except OSError as e:
        _warn(f"write current-session.json: {e}")


def inject_session_id(server: str | None = None) -> None:
    event = _read_stdin_json()
    sid = _str_field(event, "session_id")
    if sid is None:
        return
    record_session(event, sid)

    tool_name = _str_field(event, "tool_name")
    if tool_name is None or not is_madcat_tool(tool_name, server):
        return
    tool_input = event.get("tool_input")
    args = dict(tool_input) if isinstance(tool_input, dict) else {}
    if SESSION_ARG in args:
        return
    args[SESSION_ARG] = sid
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": args}}
    print(_dumps(out))


# ---------------------------------------------------------------------------
# SessionStart: welcome
# ---------------------------------------------------------------------------


def session_start(name: str, cart_tag: str | None = None) -> None:
    """Entry point for ``madcat claude hooks session-start <name>``."""
    if name != "welcome":
        print(
            f"madcat claude hooks session-start: unknown hook `{name}` (known: welcome)",
            file=sys.stderr,
        )
        return
    try:
        welcome(cart_tag)
    except Exception as e:  # noqa: BLE001 — a hook must never fail the session
        _warn(f"welcome: {e}")


def welcome(cart_override: str | None = None) -> None:
    event = _read_stdin_json()
    source = _str_field(event, "source") or "startup"
    tag = cart_override or cart.active_cart_tag()
    doc = cart.load_cart(tag) if tag else None
    if tag and doc is None:
        _warn(f"cart `{tag}` has no file at {cart.cart_path(tag)}")
    persona = Persona.from_cart(tag, doc) if tag and doc is not None else None

    # After compaction the conversation already happened; only re-anchor the essentials.
    if source == "compact":
        sys.stdout.write(compact_reminder(persona))
        return
    if path := config.claude_welcome_override():
        try:
            sys.stdout.write(_with_newline(path.read_text()))
            return
        except OSError as e:
            _warn(f"cannot read {path}: {e}")
    text = welcome_prompt(source, persona, cart_roster(tag))
    if len(text) > WELCOME_CAP:
        _warn(f"welcome prompt is {len(text)} chars; Claude Code keeps the first {WELCOME_CAP}")
    sys.stdout.write(_with_newline(text))


def _with_newline(s: str) -> str:
    return s if s.endswith("\n") else s + "\n"


def _scalar(v: Any) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return ", ".join(_scalar(x) for x in v)
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def _nonempty_str(table: Any, key: str) -> str | None:
    v = table.get(key) if isinstance(table, dict) else None
    return v.strip() or None if isinstance(v, str) else None


def _title_case(s: str) -> str:
    return s[:1].upper() + s[1:]


@dataclass
class Persona:
    """What the welcome prompt needs from a cart file."""

    tag: str
    name: str
    tagline: str | None = None
    prompt: str | None = None
    # ``[persona.<section>]`` tables (operator, behavior, protocols, …) as (title, rows).
    sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    default_voice: str | None = None
    tts_enabled: bool = False
    tts_lock: str | None = None
    # Cart's own greeting rule (``welcome`` under ``[persona]`` or top level).
    welcome: str | None = None

    @classmethod
    def from_cart(cls, tag: str, doc: dict[str, Any]) -> Persona:
        persona = doc.get("persona")
        persona = persona if isinstance(persona, dict) else {}

        # Keys sort like the Rust toml::Table (a BTreeMap), so both ports render the same prompt.
        sections = [
            (key, [(rk, _scalar(rv)) for rk, rv in sorted(table.items()) if not isinstance(rv, dict)])
            for key, table in sorted(persona.items())
            if isinstance(table, dict)
        ]
        sections = [(k, rows) for k, rows in sections if rows]

        def rank(k: str) -> int:
            return SECTION_ORDER.index(k) if k in SECTION_ORDER else len(SECTION_ORDER)

        sections.sort(key=lambda s: (rank(s[0]), s[0]))

        tts_enabled = doc.get("tts_enabled")
        return cls(
            tag=tag,
            name=_nonempty_str(persona, "name") or tag,
            tagline=_nonempty_str(persona, "tagline"),
            prompt=_nonempty_str(persona, "prompt"),
            sections=sections,
            default_voice=_nonempty_str(persona, "default_voice") or _nonempty_str(doc, "default_voice"),
            tts_enabled=tts_enabled if isinstance(tts_enabled, bool) else False,
            tts_lock=_nonempty_str(doc, "tts_session_lock"),
            welcome=_nonempty_str(persona, "welcome") or _nonempty_str(doc, "welcome"),
        )

    def persona_block(self) -> str:
        out = "## Active persona\n\n"
        if self.tagline:
            out += f"You are **{self.name}** — {self.tagline}.\n"
        else:
            out += f"You are **{self.name}**.\n"
        if self.prompt:
            out += f"{self.prompt}\n"
        for title, rows in self.sections:
            out += f"\n**{_title_case(title)}**\n"
            for k, v in rows:
                out += f"- {k.replace('_', ' ')}: {v}\n"
        out += "\n"
        if self.default_voice:
            out += f"Default voice: `{self.default_voice}`. "
        if self.tts_enabled and self.tts_lock:
            out += f"TTS: enabled; the session lock file is `{self.tts_lock}`.\n"
        elif self.tts_enabled:
            out += "TTS: enabled.\n"
        else:
            out += "TTS: not enabled for this cart.\n"
        out += "\nStay in character. Never discuss voice synthesis, TTS, carts, or audio plumbing.\n"
        return out


def cart_roster(active: str | None) -> list[str]:
    """``Name (`tag`)`` for every cart that carries a persona, for the "addressed by name" rule.
    The active cart is left out."""
    try:
        tags = cart.list_cart_tags()
    except OSError:
        return []
    roster = []
    for t in tags:
        if t == active:
            continue
        persona = (cart.load_cart(t) or {}).get("persona")
        name = persona.get("name") if isinstance(persona, dict) else None
        if isinstance(name, str):
            roster.append(f"{name} (`{t}`)")
    return roster


def compact_reminder(p: Persona | None) -> str:
    if p:
        who = f"the active persona cart `{p.tag}` ({p.name}), its voice,"
    else:
        who = "the active persona cart, its voice,"
    return (
        "# MADCAT: context was compacted\n\n"
        f"Re-anchor before continuing: {who} and the operator's preferences still apply. "
        "If the cart identity is no longer in context, run `whoami_info` and `cart_info` again. "
        "Verify you still hold the TTS lock (`prime_status`) before any `tts_speak`. "
        "Do not re-run the full boot sequence or re-greet the operator.\n"
    )


def welcome_prompt(source: str, p: Persona | None, roster: list[str]) -> str:
    out = f"# MADCAT boot (session source: {source})\n\n"
    if p:
        out += (
            "You are running with the MADCAT MCP server. "
            f"The active persona cart is `{p.tag}`.\n\n{p.persona_block()}\n"
        )
    else:
        out += (
            "You are running with the MADCAT MCP server. No persona cart is loaded "
            "(tts-state.toml names none, or its cart file is missing); "
            "keep the default persona `whoami_info` reports.\n\n"
        )

    out += (
        "## Boot sequence\n\n"
        "Before answering the operator's first message, run the boot sequence. Load every "
        'MADCAT MCP tool you need in a single ToolSearch "select:" call; '
        "the server's tools are deferred.\n\n"
        "1. **Identity**: call `whoami_info`. It returns who you are, the mesh topology, "
        "and the comms rules. Those rules govern the whole session.\n"
    )

    roster_text = f" ({', '.join(roster)})" if roster else ""
    if p:
        out += (
            "2. **Persona**: the cart above is already active; do not reload it. Only if the "
            f"operator addresses you by another cart's persona name{roster_text}, `cart_load` that "
            "cart and adopt its voice and language.\n"
        )
    else:
        out += (
            f"2. **Persona**: if the operator addresses you by a cart's persona name{roster_text}, "
            "`cart_load` that cart and adopt its voice and language. Otherwise keep the default "
            "persona from `whoami_info`.\n"
        )

    out += (
        "3. **Memory**: make one `memory_recall` call for operator preferences "
        "(subject user.preference, limit 3). `query` is always a non-empty string.\n"
    )

    if p and p.tts_enabled:
        out += (
            "4. **TTS**: this cart has TTS enabled. If no other session holds the lock, claim it "
            "with `prime_claim`. Check `prime_status` before every `tts_speak`.\n"
        )
    elif p:
        out += "4. **TTS**: this cart has no TTS. Do not claim the lock or speak.\n"
    else:
        out += (
            "4. **TTS**: if the cart has `tts_enabled` and no other session holds the lock, "
            "claim it with `prime_claim`. Check `prime_status` before every `tts_speak`.\n"
        )

    out += "\n"
    if p and p.welcome:
        out += f"{p.welcome}\n"
    else:
        out += (
            "Then greet the operator in one or two lines, in the active persona's voice and "
            "language, and wait for instructions. State the persona and whether you hold TTS. "
            "Do not narrate the boot steps themselves.\n"
        )

    out += (
        "\nThroughout the session: search `index_search` before reading files in an indexed "
        "project, use `memory_recall` at the point of need rather than speculatively, "
        "and `memory_store` decisions worth keeping. Core is the primary communications "
        "channel: stay responsive, queue work, confirm, move on.\n"
    )
    return out
