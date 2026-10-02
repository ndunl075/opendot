"""`opendot doctor`: one line per health check for the service, sign-in, keychain, connectors, disk and backups.

ARCHITECTURE.md section 11 and M4 task 4.6. Every check returns ``ok``, ``warn``, ``fail`` or
``skipped``, with what to do about it. Plain ``opendot doctor`` exits non-zero when any check fails.
``opendot doctor --ci`` runs only the checks that need no user account, no running daemon, no
keychain secret and no network, against a fresh temporary database; the checks that need the user's
real setup are reported as "skipped (needs your setup)", which is not a failure.

Nothing here makes a network call to OpenAI or anyone else. The only connection is the local probe
of the daemon on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Literal, TextIO

from .config import chatgpt_state_path as default_chatgpt_state_path

Status = Literal["ok", "warn", "fail", "skipped"]

LOW_DISK_BYTES = 1024**3
BACKUP_MAX_AGE = timedelta(days=7)
BACKUP_SUFFIX = ".opendot-backup"
SKIPPED_NEEDS_SETUP = "skipped (needs your setup)"
HEADLESS_TOKEN_NAME = "opendot-token"

HEADLESS_EXPLANATION = (
    "The access token comes from a file, not the OS keychain. That is the one allowed exception, for "
    "servers with no keychain (ARCHITECTURE.md section 11): protect the file (mode 0600, or a "
    "systemd-creds / Docker secret) and never share this server with other people."
)


@dataclass(frozen=True)
class CheckResult:
    id: str
    name: str
    status: Status
    detail: str
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "status": self.status, "detail": self.detail, "fix": self.fix}


@dataclass(frozen=True)
class ServiceInfo:
    installed: bool | None
    detail: str = ""


@dataclass(frozen=True)
class HealthProbe:
    """What the daemon answered on /v1/health: an HTTP status and whether the body was our JSON."""

    status: int
    json_body: bool


@dataclass(frozen=True)
class SignInInfo:
    connected: bool
    plan_granted: bool
    paused: bool


def _default_keyring_backend() -> str:
    import keyring

    backend = keyring.get_keyring()
    return f"{type(backend).__module__}.{type(backend).__name__}"


def _default_secret_get(name: str) -> str | None:
    from .secret_store import SystemKeyringSecretStore

    return SystemKeyringSecretStore().get_optional(name)


def _default_probe(url: str) -> HealthProbe:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - loopback URL built by us
            body = response.read(65536)
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read(65536)
        status = error.code
    try:
        parsed = json.loads(body)
    except ValueError:
        return HealthProbe(status, False)
    return HealthProbe(status, isinstance(parsed, dict))


def _default_service_status() -> ServiceInfo | None:
    """Ask ``opendot_core.service`` (task 4.1, built separately) whether the service is installed.

    Returns ``None`` when that module does not exist. The status shape is read defensively, since
    this module must not depend on a sibling task's exact API.
    """
    try:
        import importlib

        module = importlib.import_module("opendot_core.service")
    except ImportError:
        return None
    probe = getattr(module, "status", None) or getattr(module, "service_status", None)
    if not callable(probe):
        return None
    result: Any = probe()
    installed: Any
    if isinstance(result, dict):
        installed = result.get("installed")
        detail = str(result.get("detail", ""))
    else:
        installed = getattr(result, "installed", None)
        detail = str(getattr(result, "detail", "") or "")
    return ServiceInfo(installed if isinstance(installed, bool) else None, detail)


def _default_sign_in(state_path: Path) -> SignInInfo:
    from .providers.chatgpt_plan import ChatGPTPlanProvider

    status = ChatGPTPlanProvider(state_path=state_path).status()  # reads keychain + state file; no network
    return SignInInfo(status.connected, status.plan_granted, status.paused)


def _default_disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _default_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class DoctorContext:
    """Everything a check reads from the outside world, injectable so tests can use fakes."""

    db_path: Path
    data_dir: Path
    backup_dir: Path | None = None
    token_file: Path | None = None
    port: int = 8765
    ci: bool = False
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    now: Callable[[], datetime] = _default_now
    keyring_backend: Callable[[], str] = _default_keyring_backend
    secret_get: Callable[[str], str | None] = _default_secret_get
    probe: Callable[[str], HealthProbe] = _default_probe
    service_status: Callable[[], ServiceInfo | None] = _default_service_status
    sign_in: Callable[[Path], SignInInfo] = _default_sign_in
    disk_free: Callable[[Path], int] = _default_disk_free
    chatgpt_state_path: Path = field(default_factory=default_chatgpt_state_path)


# --------------------------------------------------------------------------- helpers


def _ok(id_: str, name: str, detail: str) -> CheckResult:
    return CheckResult(id_, name, "ok", detail)


def _skip(id_: str, name: str) -> CheckResult:
    return CheckResult(id_, name, "skipped", SKIPPED_NEEDS_SETUP)


def _packaged_migrations() -> list[str]:
    root = files("opendot_core.migrations")
    return sorted(path.name for path in root.iterdir() if path.name.endswith(".sql"))


def _existing_dir(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _age_text(age: timedelta) -> str:
    if age.days >= 1:
        return f"{age.days} day(s) ago"
    hours = int(age.total_seconds() // 3600)
    return f"{hours} hour(s) ago" if hours else "less than an hour ago"


def _token_file_in_use(ctx: DoctorContext) -> Path | None:
    if ctx.token_file is not None:
        return ctx.token_file
    configured = ctx.env.get("OPENDOT_TOKEN_FILE")
    if configured:
        return Path(configured)
    credentials = ctx.env.get("CREDENTIALS_DIRECTORY")
    if credentials and (Path(credentials) / HEADLESS_TOKEN_NAME).is_file():
        return Path(credentials) / HEADLESS_TOKEN_NAME
    docker_secret = Path(ctx.env.get("OPENDOT_DOCKER_SECRETS_DIR", "/run/secrets")) / HEADLESS_TOKEN_NAME
    if docker_secret.is_file():
        return docker_secret
    return None


def _keychain_is_real(backend: str) -> bool:
    lowered = backend.lower()
    return not (".fail." in lowered or ".null." in lowered or lowered.endswith(("fail.keyring", "null.keyring")))


# --------------------------------------------------------------------------- checks


def check_database(ctx: DoctorContext) -> CheckResult:
    name = "database"
    if not ctx.db_path.exists():
        return CheckResult("database", name, "warn", f"no database yet at {ctx.db_path}", "run `opendot init` (or start OpenDot once)")
    try:
        connection = sqlite3.connect(f"file:{ctx.db_path.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        return CheckResult("database", name, "fail", f"cannot open {ctx.db_path}: {error}", "check the file's permissions, or restore a backup")
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if not quick or quick[0] != "ok":
            return CheckResult("database", name, "fail", f"integrity check failed: {quick[0] if quick else 'no answer'}", "restore the newest backup (`opendot backup-restore-propose`)")
        try:
            applied = {row[0] for row in connection.execute("SELECT filename FROM schema_migrations")}
        except sqlite3.Error:
            return CheckResult("database", name, "fail", "opens, but has no migration record", "run `opendot init`")
    except sqlite3.Error as error:
        return CheckResult("database", name, "fail", f"cannot read {ctx.db_path}: {error}", "check the file's permissions, or restore a backup")
    finally:
        connection.close()
    packaged = _packaged_migrations()
    pending = [item for item in packaged if item not in applied]
    if pending:
        return CheckResult("database", name, "fail", f"{len(pending)} migration(s) not applied (next: {pending[0]})", "run `opendot init` to migrate")
    unknown = sorted(applied - set(packaged))
    if unknown:
        return CheckResult("database", name, "warn", f"database has migrations this build does not ship: {', '.join(unknown)}", "update OpenDot; this database came from a newer version")
    return _ok("database", name, f"opens, integrity ok, {len(applied)} migrations applied (current)")


def check_migration_files(ctx: DoctorContext) -> CheckResult:
    """Packaged migrations are numbered 1..N with no gap or duplicate (a build problem, not a user one)."""
    name = "migration files"
    versions = [int(item.split("_", 1)[0]) for item in _packaged_migrations()]
    if versions != list(range(1, len(versions) + 1)):
        return CheckResult("migration-files", name, "fail", f"packaged migrations are not 1..{len(versions)} without gaps", "reinstall OpenDot")
    return _ok("migration-files", name, f"{len(versions)} packaged migrations, contiguous")


def check_audit_chain(ctx: DoctorContext) -> CheckResult:
    from .audit import AuditLog
    from .db import Database

    name = "audit log"
    try:
        verified = AuditLog(Database(ctx.db_path)).verify()
    except Exception as error:  # noqa: BLE001 - any failure here is the finding
        return CheckResult("audit-chain", name, "fail", f"could not verify the audit chain: {error}", "restore a backup")
    if not verified:
        return CheckResult("audit-chain", name, "fail", "the audit hash chain does not verify", "restore a backup and review the audit log")
    return _ok("audit-chain", name, "hash chain verifies")


def check_backup_roundtrip(ctx: DoctorContext) -> CheckResult:
    """Make and verify an encrypted backup with a throwaway key (CI-safe: no keychain secret)."""
    from .backup import EncryptedBackupService
    from .db import Database
    from .policy import ApprovalService

    name = "backup round trip"
    try:
        database = Database(ctx.db_path)
        service = EncryptedBackupService(database, ApprovalService(database))
        key = service.generate_key()
        target = ctx.db_path.parent / "doctor-roundtrip.opendot-backup"
        service.create(target, encoded_key=key)
        report = service.verify_restore(target, encoded_key=key)
    except Exception as error:  # noqa: BLE001
        return CheckResult("backup-roundtrip", name, "fail", f"backup or restore rehearsal crashed: {error}", "reinstall OpenDot")
    if not report.ok:
        return CheckResult("backup-roundtrip", name, "fail", f"restore rehearsal failed: {report.failure}", "reinstall OpenDot")
    return _ok("backup-roundtrip", name, "an encrypted backup restores into a throwaway copy")


def check_keychain(ctx: DoctorContext) -> CheckResult:
    name = "keychain"
    headless = _token_file_in_use(ctx) is not None
    try:
        backend = ctx.keyring_backend()
    except Exception as error:  # noqa: BLE001 - keyring import or backend discovery can fail many ways
        backend = f"unavailable ({error})"
        real = False
    else:
        real = _keychain_is_real(backend)
    if real:
        return _ok("keychain", name, f"OS keychain usable ({backend})")
    if headless:
        return CheckResult("keychain", name, "warn", f"no usable OS keychain ({backend}); a secrets file is in use instead", "expected on a server; see deploy/README.md")
    return CheckResult(
        "keychain", name, "fail", f"no usable OS keychain ({backend})",
        "install/unlock your OS keychain (Linux: gnome-keyring or KeePassXC Secret Service), or on a server use --token-file (deploy/README.md)",
    )


def check_api_token(ctx: DoctorContext) -> CheckResult:
    from .api.serve_cli import TOKEN_SECRET

    name = "access token"
    token_file = _token_file_in_use(ctx)
    if token_file is not None:
        try:
            present = bool(token_file.read_text(encoding="utf-8").strip())
        except OSError as error:
            return CheckResult("api-token", name, "fail", f"cannot read token file {token_file}: {error}", "fix the path or permissions of the token file")
        if not present:
            return CheckResult("api-token", name, "fail", f"token file {token_file} is empty", "write a token into it (`opendot api-token show` prints one)")
        return _ok("api-token", name, f"read from token file {token_file}")
    try:
        value = ctx.secret_get(TOKEN_SECRET)
    except Exception as error:  # noqa: BLE001
        return CheckResult("api-token", name, "fail", f"cannot read the keychain: {error}", "fix the keychain first")
    if not value:
        return CheckResult("api-token", name, "warn", "no access token in the keychain yet", "run `opendot serve` or `opendot api-token show` once to create it")
    return _ok("api-token", name, "present in the OS keychain")


def check_secrets_file(ctx: DoctorContext) -> CheckResult:
    name = "secrets file"
    token_file = _token_file_in_use(ctx)
    if token_file is None:
        return _ok("secrets-file", name, "no headless secrets file; secrets are in the OS keychain")
    detail = f"{HEADLESS_EXPLANATION} (file: {token_file})"
    fix = "keep it readable by this user only (chmod 600) and re-check OpenAI's terms before promoting a server setup"
    if os.name == "posix" and token_file.exists():
        mode = stat.S_IMODE(token_file.stat().st_mode)
        if mode & 0o077:
            return CheckResult("secrets-file", name, "warn", f"{detail} It is readable by other users (mode {mode:04o}).", "run `chmod 600` on it")
    return CheckResult("secrets-file", name, "warn", detail, fix)


def check_daemon(ctx: DoctorContext) -> CheckResult:
    name = "daemon"
    url = f"http://127.0.0.1:{ctx.port}/v1/health"
    try:
        answer = ctx.probe(url)
    except (OSError, urllib.error.URLError):
        return CheckResult("daemon", name, "warn", f"nothing answers on 127.0.0.1:{ctx.port}", "start it: `opendot serve` (or `opendot service install` for always-on)")
    if answer.status == 200 or (answer.status == 401 and answer.json_body):
        word = "answers (health ok)" if answer.status == 200 else "answers (needs the access token, as expected)"
        return _ok("daemon", name, f"{word} on 127.0.0.1:{ctx.port}")
    return CheckResult("daemon", name, "warn", f"127.0.0.1:{ctx.port} answered HTTP {answer.status}, which is not OpenDot's reply", "another program may own the port; stop it or use `opendot serve --port`")


def check_service(ctx: DoctorContext) -> CheckResult:
    name = "service"
    try:
        info = ctx.service_status()
    except Exception as error:  # noqa: BLE001
        return CheckResult("service", name, "warn", f"could not read the service status: {error}", "run `opendot service status`")
    if info is None:
        return CheckResult("service", name, "warn", "service command not available", "update OpenDot to a version with `opendot service`")
    if info.installed is None:
        return CheckResult("service", name, "warn", f"service status not understood {info.detail}".strip(), "run `opendot service status`")
    if not info.installed:
        return CheckResult("service", name, "warn", "the always-on service is not installed", "optional: `opendot service install` to keep OpenDot running")
    return _ok("service", name, f"installed {info.detail}".strip())


def check_sign_in(ctx: DoctorContext) -> CheckResult:
    name = "ChatGPT sign-in"
    try:
        state = ctx.sign_in(ctx.chatgpt_state_path)
    except Exception as error:  # noqa: BLE001
        return CheckResult("chatgpt", name, "warn", f"could not read the sign-in state: {error}", "fix the keychain first")
    if not state.connected:
        return CheckResult("chatgpt", name, "warn", "signed out", "open OpenDot and choose Continue with ChatGPT (Plus or Pro)")
    if not state.plan_granted:
        return CheckResult("chatgpt", name, "warn", "signed in, but the ChatGPT plan was not granted (Free and Go plans cannot be used)", "upgrade to Plus or Pro, then sign in again")
    if state.paused:
        return CheckResult("chatgpt", name, "warn", "signed in, paused at the usage limit", "raise OpenDot's limit in ChatGPT Settings > Usage (Manage usage), then resume")
    return _ok("chatgpt", name, "signed in with the ChatGPT plan")


def check_connectors(ctx: DoctorContext) -> list[CheckResult]:
    from .connector_health import connector_health
    from .db import Database

    if not ctx.db_path.exists():
        return [CheckResult("connectors", "connectors", "warn", "no database yet, so no connectors are set up", "run `opendot init`")]
    try:
        rows = connector_health(Database(ctx.db_path), now=ctx.now())
    except Exception as error:  # noqa: BLE001
        return [CheckResult("connectors", "connectors", "fail", f"cannot read connector health: {error}", "see the database check")]
    if not rows:
        return [_ok("connectors", "connectors", "none connected yet (add Gmail, Calendar or GitHub in Settings)")]
    results = []
    for row in rows:
        id_ = f"connector:{row.connector}:{row.account}"
        label = f"connector {row.connector} ({row.account})"
        if row.state == "ok":
            results.append(_ok(id_, label, "synced recently"))
        elif row.state == "stale":
            results.append(CheckResult(id_, label, "warn", "stale: last sync was more than a day ago", "open Settings > Apps and sync now; check that the service is running"))
        elif row.state == "never_synced":
            results.append(CheckResult(id_, label, "warn", "never synced", "open Settings > Apps and sync now"))
        else:
            results.append(CheckResult(id_, label, "warn", f"error: {(row.last_error or '')[:120]}", "reconnect the app in Settings > Apps"))
    return results


def check_disk(ctx: DoctorContext) -> CheckResult:
    name = "disk space"
    target = _existing_dir(ctx.data_dir)
    try:
        free = ctx.disk_free(target)
    except OSError as error:
        return CheckResult("disk", name, "fail", f"cannot read free space for {target}: {error}", "check that the data folder exists and is readable")
    gib = free / 1024**3
    if free < LOW_DISK_BYTES:
        return CheckResult("disk", name, "warn", f"only {gib:.2f} GiB free where OpenDot keeps its data", "free some space; SQLite and backups need room to grow")
    return _ok("disk", name, f"{gib:.1f} GiB free where OpenDot keeps its data")


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def check_backups(ctx: DoctorContext) -> list[CheckResult]:
    from .backup import read_backup_status

    status = read_backup_status(ctx.data_dir)
    now = ctx.now()
    configured = ctx.env.get("OPENDOT_BACKUP_DIR")
    directory = ctx.backup_dir or (Path(configured) if configured else ctx.data_dir / "backups")
    newest: datetime | None = None
    last = status.get("last_backup")
    if isinstance(last, dict):
        newest = _parse_time(last.get("at"))
    if directory.is_dir():
        for item in directory.glob(f"*{BACKUP_SUFFIX}"):
            stamp = datetime.fromtimestamp(item.stat().st_mtime, UTC)
            if newest is None or stamp > newest:
                newest = stamp
    if newest is None:
        backup = CheckResult("backups", "backups", "warn", f"no backup found (looked in {directory})", "run `opendot backup-key-generate` then `opendot backup-create --output <folder>/opendot.opendot-backup`")
    else:
        age = now - newest
        if age > BACKUP_MAX_AGE:
            backup = CheckResult("backups", "backups", "warn", f"last backup was {_age_text(age)} (older than 7 days)", "run `opendot backup-create` and schedule it nightly")
        else:
            backup = _ok("backups", "backups", f"last backup {_age_text(age)}")
    drill = status.get("last_drill")
    if not isinstance(drill, dict):
        rehearsal = CheckResult("restore-drill", "restore drill", "warn", "a restore drill has never run", "run `opendot backup-verify --latest-in <backup folder>`")
    elif not drill.get("ok"):
        rehearsal = CheckResult("restore-drill", "restore drill", "fail", "the last restore drill FAILED", "run `opendot backup-verify` and make a fresh backup")
    else:
        when = _parse_time(drill.get("at"))
        text = _age_text(now - when) if when else "at an unknown time"
        rehearsal = _ok("restore-drill", "restore drill", f"last drill passed {text}")
    return [backup, rehearsal]


# --------------------------------------------------------------------------- runner


def run_checks(ctx: DoctorContext) -> list[CheckResult]:
    """Run every check for the mode in ``ctx.ci``. In CI mode setup-dependent checks are skipped."""
    results: list[CheckResult] = [check_database(ctx)]
    if ctx.ci:
        results += [check_migration_files(ctx), check_audit_chain(ctx), check_backup_roundtrip(ctx)]
        results += check_connectors(ctx)
        results.append(check_disk(ctx))
        for id_, name in (
            ("keychain", "keychain"),
            ("api-token", "access token"),
            ("secrets-file", "secrets file"),
            ("daemon", "daemon"),
            ("service", "service"),
            ("chatgpt", "ChatGPT sign-in"),
            ("backups", "backups"),
            ("restore-drill", "restore drill"),
        ):
            results.append(_skip(id_, name))
        return results
    results += [check_keychain(ctx), check_api_token(ctx), check_secrets_file(ctx), check_daemon(ctx), check_service(ctx), check_sign_in(ctx)]
    results += check_connectors(ctx)
    results.append(check_disk(ctx))
    results += check_backups(ctx)
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(item.status == "fail" for item in results) else 0


def format_line(result: CheckResult) -> str:
    tag = {"ok": "ok     ", "warn": "WARN   ", "fail": "FAIL   ", "skipped": "skipped"}[result.status]
    line = f"{tag} {result.name}: {result.detail}"
    if result.fix and result.status in ("warn", "fail"):
        line += f" -> {result.fix}"
    return line


def register(subparsers: Any) -> None:
    doctor = subparsers.add_parser("doctor", help="check the service, sign-in, keychain, connectors, disk and backups")
    doctor.add_argument("--ci", action="store_true", help="only checks that need no account, daemon, keychain secret or network, on a fresh temporary database")
    doctor.add_argument("--json", action="store_true", help="print machine-readable results")
    doctor.add_argument("--port", type=int, default=8765, help="daemon port to probe (default 8765)")
    doctor.add_argument("--token-file", type=Path, help="the access-token file `opendot serve --token-file` uses (headless servers)")
    doctor.add_argument("--backup-dir", type=Path, help="folder holding your backups (default: OPENDOT_BACKUP_DIR or <data folder>/backups)")


def run(args: argparse.Namespace, *, ctx: DoctorContext | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    temp: tempfile.TemporaryDirectory[str] | None = None
    try:
        if ctx is None:
            from .config import Settings
            from .db import Database

            if args.ci:
                temp = tempfile.TemporaryDirectory(prefix="opendot-doctor-", ignore_cleanup_errors=True)
                db_path = Path(temp.name) / "opendot.db"
                Database(db_path).migrate()
                ctx = DoctorContext(db_path=db_path, data_dir=db_path.parent, ci=True, port=args.port)
            else:
                db_path = Settings.from_environment(Path(args.db) if args.db else None).database_path
                ctx = DoctorContext(
                    db_path=db_path, data_dir=db_path.resolve().parent, port=args.port,
                    token_file=args.token_file, backup_dir=args.backup_dir,
                )
        results = run_checks(ctx)
    finally:
        if temp is not None:
            gc.collect()  # Windows keeps SQLite files locked until unreferenced connections are collected
            temp.cleanup()
    code = exit_code(results)
    if args.json:
        out.write(json.dumps({"mode": "ci" if ctx.ci else "full", "ok": code == 0, "checks": [item.as_dict() for item in results]}, indent=2) + "\n")
    else:
        for item in results:
            out.write(format_line(item) + "\n")
        counts = {s: sum(1 for item in results if item.status == s) for s in ("ok", "warn", "fail", "skipped")}
        out.write(f"{counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail, {counts['skipped']} skipped\n")
    return code


__all__ = ["CheckResult", "DoctorContext", "HealthProbe", "ServiceInfo", "SignInInfo", "register", "run", "run_checks"]
