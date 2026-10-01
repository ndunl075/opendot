import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opendot_core import doctor
from opendot_core.backup import record_backup_event
from opendot_core.cli import build_parser, main
from opendot_core.db import Database
from opendot_core.doctor import DoctorContext, HealthProbe, ServiceInfo, SignInInfo

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GIB = 1024**3


def make_ctx(tmp_path: Path, **overrides) -> DoctorContext:
    db_path = tmp_path / "data" / "opendot.db"
    Database(db_path).migrate()
    values = dict(
        db_path=db_path,
        data_dir=db_path.parent,
        env={},
        now=lambda: NOW,
        keyring_backend=lambda: "keyring.backends.Windows.WinVaultKeyring",
        secret_get=lambda name: "token",
        probe=lambda url: HealthProbe(401, True),
        service_status=lambda: ServiceInfo(True, "running"),
        sign_in=lambda path: SignInInfo(True, True, False),
        disk_free=lambda path: 50 * GIB,
    )
    values.update(overrides)
    return DoctorContext(**values)


def by_id(results, id_):
    return next(item for item in results if item.id == id_)


def run_all(ctx: DoctorContext):
    return {item.id: item for item in doctor.run_checks(ctx)}


# ---- database


def test_database_ok_and_current(tmp_path: Path) -> None:
    assert doctor.check_database(make_ctx(tmp_path)).status == "ok"


def test_database_missing_is_a_warning_with_a_fix(tmp_path: Path) -> None:
    result = doctor.check_database(make_ctx(tmp_path, db_path=tmp_path / "none.db"))
    assert result.status == "warn" and "opendot init" in result.fix


def test_database_with_unapplied_migration_fails(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    with Database(ctx.db_path).connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = (SELECT MAX(version) FROM schema_migrations)")
    result = doctor.check_database(ctx)
    assert result.status == "fail" and "not applied" in result.detail


def test_database_that_is_not_sqlite_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    assert doctor.check_database(make_ctx(tmp_path, db_path=bad)).status == "fail"


# ---- keychain and token


def test_keychain_real_backend_is_ok(tmp_path: Path) -> None:
    assert doctor.check_keychain(make_ctx(tmp_path)).status == "ok"


@pytest.mark.parametrize("backend", ["keyring.backends.fail.Keyring", "keyring.backends.null.Keyring"])
def test_keychain_fail_or_null_backend_fails(tmp_path: Path, backend: str) -> None:
    result = doctor.check_keychain(make_ctx(tmp_path, keyring_backend=lambda: backend))
    assert result.status == "fail" and result.fix


def test_keychain_unavailable_with_a_token_file_is_only_a_warning(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("abc")
    ctx = make_ctx(tmp_path, keyring_backend=lambda: "keyring.backends.fail.Keyring", token_file=token)
    assert doctor.check_keychain(ctx).status == "warn"


def test_api_token_present_missing_and_unreadable_keychain(tmp_path: Path) -> None:
    assert doctor.check_api_token(make_ctx(tmp_path)).status == "ok"
    assert doctor.check_api_token(make_ctx(tmp_path, secret_get=lambda name: None)).status == "warn"

    def broken(name: str) -> str:
        raise RuntimeError("locked")

    assert doctor.check_api_token(make_ctx(tmp_path, secret_get=broken)).status == "fail"


def test_api_token_uses_the_right_secret_name(tmp_path: Path) -> None:
    seen: list[str] = []
    doctor.check_api_token(make_ctx(tmp_path, secret_get=lambda name: seen.append(name) or "x"))
    assert seen == ["opendot-api-token"]


# ---- headless secrets file


def test_headless_token_file_is_a_warning_with_the_section_11_explanation(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("abc")
    if os.name == "posix":
        token.chmod(0o600)
    result = doctor.check_secrets_file(make_ctx(tmp_path, token_file=token))
    assert result.status == "warn"
    assert "never share this server" in result.detail and "section 11" in result.detail
    assert doctor.check_api_token(make_ctx(tmp_path, token_file=token)).status == "ok"


def test_systemd_credentials_and_docker_secret_are_detected(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "opendot-token").write_text("abc")
    assert doctor.check_secrets_file(make_ctx(tmp_path, env={"CREDENTIALS_DIRECTORY": str(creds)})).status == "warn"
    assert doctor.check_secrets_file(make_ctx(tmp_path, env={"OPENDOT_DOCKER_SECRETS_DIR": str(creds)})).status == "warn"
    assert doctor.check_secrets_file(make_ctx(tmp_path, env={"OPENDOT_DOCKER_SECRETS_DIR": str(tmp_path / "nope")})).status == "ok"


def test_empty_or_missing_token_file_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("\n")
    assert doctor.check_api_token(make_ctx(tmp_path, token_file=empty)).status == "fail"
    assert doctor.check_api_token(make_ctx(tmp_path, token_file=tmp_path / "missing")).status == "fail"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_world_readable_token_file_warns_about_the_mode(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("abc")
    token.chmod(0o644)
    result = doctor.check_secrets_file(make_ctx(tmp_path, token_file=token))
    assert "0644" in result.detail and "chmod 600" in result.fix


# ---- daemon and service


def test_daemon_json_401_and_200_are_ok(tmp_path: Path) -> None:
    assert doctor.check_daemon(make_ctx(tmp_path, probe=lambda url: HealthProbe(401, True))).status == "ok"
    assert doctor.check_daemon(make_ctx(tmp_path, probe=lambda url: HealthProbe(200, True))).status == "ok"


def test_daemon_probe_targets_loopback_only(tmp_path: Path) -> None:
    seen: list[str] = []
    doctor.check_daemon(make_ctx(tmp_path, port=9000, probe=lambda url: seen.append(url) or HealthProbe(200, True)))
    assert seen == ["http://127.0.0.1:9000/v1/health"]


def test_daemon_down_and_stranger_are_warnings(tmp_path: Path) -> None:
    def refused(url: str) -> HealthProbe:
        raise ConnectionRefusedError()

    assert doctor.check_daemon(make_ctx(tmp_path, probe=refused)).status == "warn"
    assert doctor.check_daemon(make_ctx(tmp_path, probe=lambda url: HealthProbe(401, False))).status == "warn"


def test_service_installed_not_installed_and_not_available(tmp_path: Path) -> None:
    assert doctor.check_service(make_ctx(tmp_path)).status == "ok"
    assert doctor.check_service(make_ctx(tmp_path, service_status=lambda: ServiceInfo(False))).status == "warn"
    unavailable = doctor.check_service(make_ctx(tmp_path, service_status=lambda: None))
    assert unavailable.status == "warn" and "service command not available" in unavailable.detail
    assert doctor.check_service(make_ctx(tmp_path, service_status=lambda: ServiceInfo(None))).status == "warn"


# ---- sign-in


def test_sign_in_states(tmp_path: Path) -> None:
    def state(connected: bool, granted: bool, paused: bool):
        return make_ctx(tmp_path, sign_in=lambda path: SignInInfo(connected, granted, paused))

    assert doctor.check_sign_in(state(True, True, False)).status == "ok"
    out = doctor.check_sign_in(state(False, False, False))
    assert out.status == "warn" and "signed out" in out.detail
    assert "usage limit" in doctor.check_sign_in(state(True, True, True)).detail
    assert "plan was not granted" in doctor.check_sign_in(state(True, False, False)).detail


def test_real_sign_in_default_makes_no_network_call(tmp_path: Path, monkeypatch) -> None:
    import httpx

    def refuse(*args, **kwargs):
        raise AssertionError("doctor must not touch the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr("opendot_core.secret_store.SystemKeyringSecretStore.get_optional", lambda self, name: None)
    info = doctor._default_sign_in(tmp_path / "state.json")
    assert info == SignInInfo(False, False, False)


# ---- connectors


def _sync_state(database: Database, connector: str, success: str | None, error: str | None) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) VALUES (?, 'a', NULL, ?, ?, ?)",
            (connector, success, error, NOW.isoformat()),
        )


def test_connectors_none_ok_stale_error_never(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    assert doctor.check_connectors(ctx)[0].status == "ok"
    database = Database(ctx.db_path)
    _sync_state(database, "gmail", (NOW - timedelta(hours=1)).isoformat(), None)
    _sync_state(database, "github", (NOW - timedelta(days=3)).isoformat(), None)
    _sync_state(database, "google_calendar", None, None)
    _sync_state(database, "slack", (NOW - timedelta(hours=1)).isoformat(), "boom")
    statuses = {item.name: item.status for item in doctor.check_connectors(ctx)}
    assert statuses["connector gmail (a)"] == "ok"
    assert statuses["connector github (a)"] == "warn"
    assert statuses["connector google_calendar (a)"] == "warn"
    assert statuses["connector slack (a)"] == "warn"


# ---- disk


def test_disk_warns_under_one_gib(tmp_path: Path) -> None:
    assert doctor.check_disk(make_ctx(tmp_path, disk_free=lambda path: GIB // 2)).status == "warn"
    assert doctor.check_disk(make_ctx(tmp_path, disk_free=lambda path: GIB)).status == "ok"


def test_disk_oserror_fails(tmp_path: Path) -> None:
    def broken(path: Path) -> int:
        raise OSError("gone")

    assert doctor.check_disk(make_ctx(tmp_path, disk_free=broken)).status == "fail"


# ---- backups


def test_backups_none_old_recent_and_drill(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    results = {item.id: item for item in doctor.check_backups(ctx)}
    assert results["backups"].status == "warn" and "no backup" in results["backups"].detail
    assert results["restore-drill"].status == "warn" and "never run" in results["restore-drill"].detail

    record_backup_event(ctx.data_dir, "last_backup", path="x", at=NOW - timedelta(days=9))
    assert by_id(doctor.check_backups(ctx), "backups").status == "warn"

    record_backup_event(ctx.data_dir, "last_backup", path="x", at=NOW - timedelta(days=1))
    record_backup_event(ctx.data_dir, "last_drill", path="x", ok=True, at=NOW - timedelta(days=2))
    results = {item.id: item for item in doctor.check_backups(ctx)}
    assert results["backups"].status == "ok" and results["restore-drill"].status == "ok"

    record_backup_event(ctx.data_dir, "last_drill", path="x", ok=False, at=NOW)
    assert by_id(doctor.check_backups(ctx), "restore-drill").status == "fail"


def test_backups_found_by_scanning_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "bk"
    folder.mkdir()
    file = folder / "a.opendot-backup"
    file.write_bytes(b"x")
    os.utime(file, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    assert by_id(doctor.check_backups(make_ctx(tmp_path, backup_dir=folder)), "backups").status == "ok"


# ---- runner, output, exit codes


def test_full_run_all_green_exits_zero_and_prints_one_line_per_check(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    record_backup_event(ctx.data_dir, "last_backup", path="x", at=NOW - timedelta(hours=2))
    record_backup_event(ctx.data_dir, "last_drill", path="x", ok=True, at=NOW - timedelta(hours=2))
    out = io.StringIO()
    args = build_parser().parse_args(["doctor"])
    assert doctor.run(args, ctx=ctx, out=out) == 0
    lines = out.getvalue().strip().splitlines()
    assert lines[-1].endswith("0 fail, 0 skipped")
    assert all(line.startswith(("ok", "WARN", "FAIL")) for line in lines[:-1])


def test_full_run_exits_nonzero_on_any_failure(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, keyring_backend=lambda: "keyring.backends.fail.Keyring")
    out = io.StringIO()
    assert doctor.run(build_parser().parse_args(["doctor"]), ctx=ctx, out=out) == 1
    assert "FAIL" in out.getvalue()


def test_warnings_alone_do_not_fail(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, probe=lambda url: (_ for _ in ()).throw(ConnectionRefusedError()))
    assert doctor.exit_code(doctor.run_checks(ctx)) == 0


def test_json_output_is_machine_readable(tmp_path: Path) -> None:
    out = io.StringIO()
    code = doctor.run(build_parser().parse_args(["doctor", "--json"]), ctx=make_ctx(tmp_path), out=out)
    payload = json.loads(out.getvalue())
    assert payload["mode"] == "full" and payload["ok"] == (code == 0)
    assert {"id", "name", "status", "detail", "fix"} <= set(payload["checks"][0])


def test_ci_mode_skips_user_setup_checks_and_passes_on_a_fresh_database(capsys) -> None:
    assert main(["doctor", "--ci"]) == 0
    output = capsys.readouterr().out
    for name in ("keychain", "access token", "daemon", "service", "ChatGPT sign-in", "backups", "restore drill"):
        assert f"skipped {name}: skipped (needs your setup)" in output
    assert "ok      database" in output and "0 fail" in output


def test_ci_mode_never_touches_keychain_network_or_the_users_database(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("not allowed in --ci")

    monkeypatch.setattr(doctor, "_default_secret_get", forbidden)
    monkeypatch.setattr(doctor, "_default_keyring_backend", forbidden)
    monkeypatch.setattr(doctor, "_default_probe", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    users_db = tmp_path / "users.db"
    assert main(["--db", str(users_db), "doctor", "--ci"]) == 0
    assert not users_db.exists()


def test_ci_mode_exits_nonzero_when_a_ci_safe_check_fails(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, ci=True)
    with Database(ctx.db_path).connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 1")
    out = io.StringIO()
    assert doctor.run(build_parser().parse_args(["doctor", "--ci"]), ctx=ctx, out=out) == 1


def test_ci_mode_low_disk_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, ci=True, disk_free=lambda path: 1)
    results = doctor.run_checks(ctx)
    assert by_id(results, "disk").status == "warn"
    assert doctor.exit_code(results) == 0


def test_cli_backup_create_and_verify_record_status(tmp_path: Path, monkeypatch, capsys) -> None:
    from opendot_core.backup import EncryptedBackupService, read_backup_status

    key = EncryptedBackupService.generate_key()
    monkeypatch.setattr("opendot_core.secret_store.SystemKeyringSecretStore.get_required", lambda self, name: key)
    database = tmp_path / "d" / "opendot.db"
    backup = tmp_path / "b" / "x.opendot-backup"
    assert main(["--db", str(database), "init"]) == 0
    assert main(["--db", str(database), "backup-create", "--output", str(backup)]) == 0
    assert main(["--db", str(database), "backup-verify", "--backup", str(backup)]) == 0
    status = read_backup_status(database.parent)
    assert status["last_backup"]["path"] == str(backup.resolve())
    assert status["last_drill"]["ok"] is True
    capsys.readouterr()
