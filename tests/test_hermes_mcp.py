from pathlib import Path

import pytest

from alfred.hermes_mcp import is_registered, profile_config_path, register

LIVE_SHAPE = """\
# Model/provider defaults for this profile.
#
# 1. This distribution's mcp.json is NOT automatically applied by
#    `hermes profile install` -- the real live mechanism is this file's own
#    `mcp_servers:` key.

providers:
  ollama-local:
    api: http://localhost:11434/v1
    api_key: ""

# mcp_servers:
#   browseros-neo:
#     url: "http://127.0.0.1:9210/mcp"

approvals:
  deny:
    - "*rm -rf*"
"""


def _config(tmp_path: Path, text: str = LIVE_SHAPE) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_commented_example_does_not_count_as_registered() -> None:
    """The live config already carried a commented `mcp_servers:` example, so
    a substring check reports success against a file that configures nothing
    -- which is very close to the original failure."""
    assert is_registered(LIVE_SHAPE) is False


def test_an_active_key_counts_as_registered() -> None:
    assert is_registered(LIVE_SHAPE + "\nmcp_servers:\n  alfred:\n    command: alfred-mcp\n")


def test_registering_adds_the_key(tmp_path: Path) -> None:
    path = _config(tmp_path)

    result = register(config_path=path)

    assert result.changed is True
    text = path.read_text(encoding="utf-8")
    assert is_registered(text)
    assert "command: alfred-mcp" in text
    assert "--client-id, hermes" in text


def test_every_comment_and_key_survives(tmp_path: Path) -> None:
    """A yaml round-trip would silently discard the comments that explain why
    this profile is configured the way it is, turning a documented file into
    an anonymous one."""
    path = _config(tmp_path)

    register(config_path=path)

    text = path.read_text(encoding="utf-8")
    for original_line in LIVE_SHAPE.splitlines():
        assert original_line in text, original_line


def test_re_running_changes_nothing(tmp_path: Path) -> None:
    path = _config(tmp_path)
    register(config_path=path)
    after_first = path.read_text(encoding="utf-8")

    second = register(config_path=path)

    assert second.changed is False
    assert path.read_text(encoding="utf-8") == after_first


def test_the_previous_file_is_kept(tmp_path: Path) -> None:
    path = _config(tmp_path)

    result = register(config_path=path)

    assert result.backup_path is not None
    assert Path(result.backup_path).read_text(encoding="utf-8") == LIVE_SHAPE


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    path = _config(tmp_path)

    result = register(config_path=path, dry_run=True)

    assert result.changed is False
    assert path.read_text(encoding="utf-8") == LIVE_SHAPE
    assert not list(tmp_path.glob("*alfred-bak*"))


def test_a_missing_profile_is_refused_rather_than_created(tmp_path: Path) -> None:
    """Writing a fresh config would produce a profile Hermes has never
    installed, which fails later and further from the cause."""
    with pytest.raises(FileNotFoundError):
        register(config_path=tmp_path / "nope" / "config.yaml")


def test_a_file_without_a_trailing_newline_is_not_corrupted(tmp_path: Path) -> None:
    path = _config(tmp_path, "providers:\n  ollama-local:\n    api_key: \"\"")

    register(config_path=path)

    text = path.read_text(encoding="utf-8")
    assert 'api_key: ""\n' in text
    assert is_registered(text)


def test_the_default_path_points_at_the_named_profile(tmp_path: Path) -> None:
    path = profile_config_path("alfred", profile_root=tmp_path)

    assert path.parent.name == "alfred"
    assert path.name == "config.yaml"
