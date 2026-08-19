import json
import sys
from pathlib import Path

import pytest

import alfred.winservice
from alfred.winservice import _repo_root, configure, load_configured_args


def _is_source_install() -> bool:
    """Whether alfred is importable from a checkout rather than site-packages.

    Mirrors what `_repo_root` looks for, so a test guarded by this cannot
    disagree with the function it is guarding.
    """
    module = Path(alfred.winservice.__file__).resolve()
    return (module.parents[2] / "pyproject.toml").exists()


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


def test_repo_root_locates_the_checkout_containing_pyproject_toml() -> None:
    """Regression test for a real bug: the Windows service used to look for
    `.alfred/service.json` relative to whatever working directory the Service
    Control Manager happened to launch `pythonservice.exe` with (typically
    %SystemRoot%\\System32), instead of the repository root `alfred
    service-configure` actually wrote it to -- so `service-configure` reported
    success, `alfred-service start` reported success, and the service still
    died immediately with "`.alfred\\service.json` does not exist".

    Only meaningful against a source checkout. The release workflow installs
    the built wheel and runs this suite against it, where `winservice.py`
    lives in site-packages and has no repository above it -- so `_repo_root`
    correctly raises instead, which the next test covers. Asserting the happy
    path there failed the v0.2.0 release build, which is precisely what
    testing the wheel rather than the source tree is for.
    """
    if not _is_source_install():
        pytest.skip("installed as a wheel; the Windows service requires an editable install")
    root = _repo_root()

    assert (root / "pyproject.toml").exists()
    assert (root / "src" / "alfred" / "winservice.py").exists()


def test_repo_root_raises_a_clear_error_when_it_cannot_find_pyproject_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import alfred.winservice as winservice_module

    fake_module_path = tmp_path / "somewhere" / "site-packages" / "alfred" / "winservice.py"
    monkeypatch.setattr(winservice_module, "__file__", str(fake_module_path))

    with pytest.raises(RuntimeError, match="could not locate the alfred-core repository root"):
        _repo_root()


@pytest.mark.skipif(sys.platform != "win32", reason="pywin32 (win32event/win32service/servicemanager) is Windows-only")
def test_service_class_can_be_built_without_actually_registering_a_service() -> None:
    """Confirms the pywin32 import and class definition succeed; SvcDoRun/
    SvcStop themselves need a real SCM context to exercise and are covered
    by manual verification (documented in README), not unit tests.

    Everything else in this file is pure filesystem logic and runs on any
    OS -- this is the one test that actually imports pywin32, which is why
    it alone is skipped off Windows (e.g. the release workflow's
    ubuntu-latest build/test step)."""
    from alfred.winservice import _build_service_class

    service_class = _build_service_class()
    assert service_class._svc_name_ == "Alfred"
    assert "Alfred" in service_class._svc_display_name_


@pytest.mark.skipif(sys.platform != "win32", reason="pywin32 (win32event/win32service/servicemanager) is Windows-only")
def test_service_class_is_a_real_module_attribute_pywin32_can_find_by_name() -> None:
    """Regression test for a real bug: when the Service Control Manager actually
    starts the service, a fresh `pythonservice.exe` process imports
    `alfred.winservice` and looks up the service class by name using the
    module/class string it stored in the registry at install time -- it does not
    reuse any object `_build_service_class()` returned during `alfred-service
    install`. A class defined only inside that function is a local variable, not
    a module attribute, so that lookup used to fail with `AttributeError: module
    'alfred.winservice' has no attribute 'AlfredWindowsService'` and the service
    terminated immediately (Windows exit code 1066, "Incorrect function") even
    though installation itself had already reported success."""
    import alfred.winservice as winservice_module

    service_class = getattr(winservice_module, "AlfredWindowsService", None)
    assert service_class is not None
    assert service_class is winservice_module._build_service_class()
    assert service_class._svc_name_ == "Alfred"
