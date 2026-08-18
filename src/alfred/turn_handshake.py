"""Per-turn tool filter and turn id, passed by file because env cannot cross.

Alfred narrows the agent's tools every turn (`hermes_tools.select_hermes_tools`,
capped at eight) and tags the turn so MCP calls can be observed. Both were
passed as environment variables on the Hermes process. Neither ever arrived.

Hermes builds a *filtered* environment for stdio MCP servers on purpose --
`tools/mcp_tool.py:_build_safe_env` keeps only a baseline allowlist, `XDG_*`,
secret-source-tagged variables, and whatever the server's own config `env:`
block names, specifically so a parent's API keys are not handed to every
subprocess. Alfred's two variables match none of those, so they were stripped
every time.

Measured rather than assumed. With `ALFRED_HERMES_MCP_TOOLS=system_status`,
Hermes was asked to list its Alfred tools and named all of them, including
`action_commit` -- the one tool section 7 says the conversational model must
never receive. And `workflow_tool_observations` stayed empty across a turn
that provably called a tool, because the turn id was stripped too.

The consequences were a silent safety regression and a dead learning loop:
every turn shipped the whole tool surface instead of the chosen eight, which
also spends context and worsens selection.

**A file, not an env var, and not the config's `env:` block.** That block is
written once and read on every spawn, so it cannot carry something that
changes per turn. A file can: Alfred writes it immediately before invoking
Hermes, and because each Telegram turn runs `hermes -z` as a fresh process
with a fresh MCP server, the server reads it at startup and gets exactly that
turn's values.

Location is derived from the database path rather than configured, since the
server already knows that and a second setting is a second thing to get
wrong -- the failure this whole module exists to repair.

Single-writer by design: Alfred's run loop handles one turn at a time. Two
concurrent turns would race on one file, so the writer stamps the turn id it
wrote and the reader returns nothing if asked to verify a different one.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .hermes_tools import HERMES_MCP_TOOL_FILTER_ENV
from .workflow_learning import WORKFLOW_TURN_ID_ENV

HANDSHAKE_FILENAME = "hermes-turn.json"


def handshake_path(database_path: Path | str) -> Path:
    return Path(database_path).parent / HANDSHAKE_FILENAME


def write(
    database_path: Path | str,
    *,
    turn_id: str | None,
    tools: frozenset[str] | None,
) -> Path:
    """Publish one turn's filter and id for the MCP server to pick up.

    ``tools=None`` means "no restriction"; an empty set means "no tools",
    which is a real state -- the casual lane runs with none. The two must stay
    distinguishable or a casual turn inherits the previous turn's tools.
    """
    path = handshake_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"turn_id": turn_id}
    payload["tools"] = None if tools is None else sorted(tools)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return path


def clear(database_path: Path | str) -> None:
    """Remove the handshake so a later spawn cannot inherit a stale turn."""
    try:
        handshake_path(database_path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # Best effort. A stale file is corrected by the next write, and
        # failing a turn over cleanup would be worse than the staleness.
        pass


@contextmanager
def published(
    database_path: Path | str,
    *,
    turn_id: str | None,
    tools: frozenset[str] | None,
) -> Iterator[Path]:
    """Publish for the duration of one agent invocation, then clean up."""
    path = write(database_path, turn_id=turn_id, tools=tools)
    try:
        yield path
    finally:
        clear(database_path)


def read_tools(database_path: Path | str) -> frozenset[str] | None:
    """The tool filter for this turn, or None when unrestricted.

    The environment is still consulted first so a direct `alfred-mcp` run
    (Claude, Cursor, the OpenAI tunnel) keeps working exactly as before; only
    the Hermes path, where the environment cannot survive, needs the file.
    """
    raw = os.environ.get(HERMES_MCP_TOOL_FILTER_ENV)
    if raw is not None:
        names = {name.strip() for name in raw.split(",") if name.strip()}
        return frozenset(names)
    payload = _payload(database_path)
    if payload is None:
        return None
    tools = payload.get("tools")
    if tools is None:
        return None
    if not isinstance(tools, list):
        return None
    return frozenset(str(name) for name in tools)


def read_turn_id(database_path: Path | str) -> str | None:
    value = os.environ.get(WORKFLOW_TURN_ID_ENV, "").strip()
    if value:
        return value
    payload = _payload(database_path)
    if payload is None:
        return None
    turn_id = payload.get("turn_id")
    return str(turn_id) if isinstance(turn_id, str) and turn_id.strip() else None


def _payload(database_path: Path | str) -> dict | None:
    try:
        raw = handshake_path(database_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # An unreadable handshake must not take the turn down with it; the
        # server simply falls back to "unrestricted", which is what it did
        # before this existed.
        return None
    return payload if isinstance(payload, dict) else None
