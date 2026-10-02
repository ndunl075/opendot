"""OpenDot's data lives in one per-user folder, never in the directory it was started from."""

from pathlib import Path

import pytest

from opendot_core import config
from opendot_core.providers.chatgpt_plan import ChatGPTPlanProvider


def test_defaults_ignore_the_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    elsewhere = tmp_path / "some-checkout"
    elsewhere.mkdir()
    monkeypatch.setenv("OPENDOT_DATA_DIR", str(data))
    monkeypatch.chdir(elsewhere)

    assert config.Settings.from_environment().database_path == data / "opendot.db"
    assert config.chatgpt_state_path() == data / "chatgpt_plan.json"
    assert ChatGPTPlanProvider()._state.path == data / "chatgpt_plan.json"
    assert not (elsewhere / ".opendot").exists()


def test_os_app_data_folder_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENDOT_DATA_DIR", raising=False)
    folder = config.data_dir()
    assert folder.is_absolute() and folder.name == "org.opendot.desktop"
    assert ".opendot" not in folder.parts


def test_explicit_database_path_still_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDOT_DB_PATH", str(tmp_path / "custom.db"))
    assert config.Settings.from_environment().database_path == tmp_path / "custom.db"
    assert config.Settings.from_environment(tmp_path / "flag.db").database_path == tmp_path / "flag.db"
