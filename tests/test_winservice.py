import json
from pathlib import Path

import pytest

from alfred.winservice import configure, load_configured_args


def test_configure_writes_the_run_args_and_load_reads_them_back(tmp_path: Path) -> None:
    alfred_dir = tmp_path / ".alfred"
    run_args = ["run", "--pair", "123:456", "--chat-id", "123"]

    config_path = configure(run_args, alfred_dir=alfred_dir)

    assert config_path == alfred_dir / "service.json"
    assert load_configured_args(alfred_dir=alfred_dir) == run_args


def test_configure_rejects_args_that_do_not_start_with_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must start with 'run'"):
        configure(["status"], alfred_dir=tmp_path / ".alfred")


def test_configure_rejects_empty_args(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must start with 'run'"):
        configure([], alfred_dir=tmp_path / ".alfred")


def test_load_configured_args_without_a_prior_configure_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="service-configure"):
        load_configured_args(alfred_dir=tmp_path / ".alfred")


def test_load_configured_args_rejects_malformed_json(tmp_path: Path) -> None:
    alfred_dir = tmp_path / ".alfred"
    alfred_dir.mkdir()
    (alfred_dir / "service.json").write_text("not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not valid JSON"):
        load_configured_args(alfred_dir=alfred_dir)


def test_load_configured_args_rejects_a_non_list_args_field(tmp_path: Path) -> None:
    alfred_dir = tmp_path / ".alfred"
    alfred_dir.mkdir()
    (alfred_dir / "service.json").write_text(json.dumps({"args": "run --pair 123:456"}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="malformed"):
        load_configured_args(alfred_dir=alfred_dir)


def test_configure_overwrites_a_previous_configuration(tmp_path: Path) -> None:
    alfred_dir = tmp_path / ".alfred"
    configure(["run", "--pair", "1:2"], alfred_dir=alfred_dir)
    configure(["run", "--pair", "3:4"], alfred_dir=alfred_dir)

    assert load_configured_args(alfred_dir=alfred_dir) == ["run", "--pair", "3:4"]


def test_service_class_can_be_built_without_actually_registering_a_service() -> None:
    """Confirms the pywin32 import and class definition succeed; SvcDoRun/
    SvcStop themselves need a real SCM context to exercise and are covered
    by manual verification (documented in README), not unit tests."""
    from alfred.winservice import _build_service_class

    service_class = _build_service_class()
    assert service_class._svc_name_ == "Alfred"
    assert "Alfred" in service_class._svc_display_name_
