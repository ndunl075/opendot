"""The server deploy files keep the daemon on 127.0.0.1 and run it as a non-root user (task 4.7).

No YAML library is a dependency, so these parse the two files line by line.
"""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _lines(name: str) -> list[str]:
    return [line.rstrip() for line in (DEPLOY / name).read_text(encoding="utf-8").splitlines()]


def _instructions(name: str) -> list[str]:
    return [line.strip() for line in _lines(name) if line.strip() and not line.strip().startswith("#")]


def test_dockerfile_runs_as_a_non_root_user() -> None:
    users = [line.split(maxsplit=1)[1].strip() for line in _instructions("Dockerfile") if line.upper().startswith("USER ")]
    assert users, "the Dockerfile must set USER"
    final = users[-1].split(":")[0]
    assert final not in ("root", "0")
    assert "useradd" in (DEPLOY / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_never_binds_all_interfaces_and_has_no_secret() -> None:
    text = "\n".join(_instructions("Dockerfile"))
    assert "0.0.0.0" not in text and "::" not in text
    assert not re.search(r"(?i)\b(token|secret|password|key)\s*=\s*\S+", text.replace("--token-file", ""))
    assert "EXPOSE" not in text.upper()
    assert "--token-file" in text and "/run/secrets/" in text


def test_compose_binds_loopback_only_and_runs_non_root() -> None:
    text = "\n".join(_instructions("compose.yaml"))
    published = re.findall(r"^\s*-\s*\"?([\d.:\[\]a-f]*:\d+:\d+|\d+:\d+)\"?\s*$", text, flags=re.MULTILINE)
    for mapping in published:
        assert mapping.startswith("127.0.0.1:"), f"port mapping must bind 127.0.0.1: {mapping}"
    if "ports:" not in text:
        # The daemon listens on 127.0.0.1 inside its namespace, so loopback-only means sharing the host's.
        assert re.search(r"^\s*network_mode:\s*host\s*$", text, flags=re.MULTILINE)
    assert "0.0.0.0" not in text
    user = re.search(r"^\s*user:\s*\"?([^\s\"]+)\"?\s*$", text, flags=re.MULTILINE)
    assert user, "compose must set a user"
    assert user.group(1).split(":")[0] not in ("root", "0")
    assert re.search(r"^\s*read_only:\s*true", text, flags=re.MULTILINE)
    assert "opendot-token" in text


def test_secrets_folder_is_git_ignored_and_systemd_unit_keeps_the_token_out_of_the_command_line() -> None:
    assert (DEPLOY / "secrets" / ".gitignore").read_text(encoding="utf-8").startswith("*")
    unit = (DEPLOY / "systemd" / "opendot.service").read_text(encoding="utf-8")
    assert "LoadCredentialEncrypted=opendot-token:" in unit
    assert "--token-file ${CREDENTIALS_DIRECTORY}/opendot-token" in unit
    assert "0.0.0.0" not in unit
