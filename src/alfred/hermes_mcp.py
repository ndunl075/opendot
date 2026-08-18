"""Register Alfred's MCP server in a Hermes profile, without a human at a prompt.

Alfred's whole agent plane depends on Hermes reaching Alfred over stdio MCP:
architecture section 7 is thirty-three tools with per-turn selection, policy
scoping, and annotations. On the live install none of it had ever run. Hermes
answered `hermes -p alfred mcp list` with "No MCP servers configured", every
learning table sat at zero rows, and not one approval had ever been created
by `mcp:hermes` -- every answer Alfred had given came from the prompt context
pack alone.

The cause was a documented manual step. The profile's own `config.yaml` says
registration must be done by running `hermes mcp add ...` once by hand,
because that command "answers an interactive 'Enable all 17 tools?' prompt it
can't be scripted past". A setup step that cannot be scripted is a setup step
that eventually does not happen, and nothing downstream noticed for weeks --
Alfred kept replying, just from context alone, which is exactly why it went
unseen.

So this writes the same `mcp_servers:` key that command produces.

**Text insertion, not a YAML round-trip.** The profile config is mostly
comments -- why Nous Portal ended up primary, why the approvals list is
deliberately broad, which upstream behaviours were verified against a real
install. `yaml.safe_load` followed by `yaml.dump` would silently discard all
of it, turning a documented file into an anonymous one. Rewriting only the
lines this owns keeps every comment and every unrelated key byte-for-byte.

Idempotent, backed up, and verifiable: re-running changes nothing, the
previous file is kept beside the original the same way Hermes's own backups
are, and the authority on whether it worked is Hermes itself rather than this
module's opinion of the file it just wrote.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

#: Where a Hermes profile lives on Windows. The profile name is appended.
DEFAULT_PROFILE_ROOT = Path.home() / "AppData" / "Local" / "hermes" / "profiles"

#: The server name inside Hermes's config, and the command it runs. `args`
#: gives the tunnel/profile its own scoped client id rather than sharing
#: `local-mcp` with Claude and Cursor, matching section 7.
SERVER_NAME = "alfred"
SERVER_COMMAND = "alfred-mcp"
SERVER_ARGS = ("--client-id", "hermes")

#: An *active* mcp_servers key: at column zero and not commented out. The live
#: config already contains a commented example, so a naive substring check
#: reports success against a file that configures nothing -- which is very
#: close to the original failure.
_ACTIVE_KEY = re.compile(r"^mcp_servers:\s*$", re.MULTILINE)

_BLOCK = f"""
# Added by `alfred hermes-mcp-register`. This key -- not this distribution's
# mcp.json -- is what Hermes actually reads, and without it Alfred's entire
# MCP tool surface is invisible to the agent.
mcp_servers:
  {SERVER_NAME}:
    command: {SERVER_COMMAND}
    args: [{", ".join(SERVER_ARGS)}]
"""


class RegistrationResult(BaseModel):
    config_path: str
    #: False when the key was already present, so a re-run is a no-op.
    changed: bool
    #: Where the previous file was copied, when one was written.
    backup_path: str | None = None
    detail: str


def profile_config_path(profile: str, *, profile_root: Path | None = None) -> Path:
    return (profile_root or DEFAULT_PROFILE_ROOT) / profile / "config.yaml"


def is_registered(config_text: str) -> bool:
    """Whether an active (uncommented) mcp_servers key is present."""
    return _ACTIVE_KEY.search(config_text) is not None


def register(
    *,
    profile: str = "alfred",
    profile_root: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> RegistrationResult:
    """Add the `mcp_servers` key to a Hermes profile config, once."""
    path = config_path or profile_config_path(profile, profile_root=profile_root)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist; install the Hermes profile before registering"
        )
    original = path.read_text(encoding="utf-8")
    if is_registered(original):
        return RegistrationResult(
            config_path=str(path),
            changed=False,
            detail="already registered; nothing to do",
        )
    if dry_run:
        return RegistrationResult(
            config_path=str(path),
            changed=False,
            detail="would append an mcp_servers key (dry run)",
        )

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".yaml.alfred-bak.{stamp}")
    # Copied rather than moved, so an interrupted write cannot leave the
    # profile without a config at all.
    shutil.copy2(path, backup)
    separator = "" if original.endswith("\n") else "\n"
    path.write_text(original + separator + _BLOCK, encoding="utf-8")
    return RegistrationResult(
        config_path=str(path),
        changed=True,
        backup_path=str(backup),
        detail=f"registered {SERVER_NAME}; restart Alfred for the agent to pick it up",
    )
