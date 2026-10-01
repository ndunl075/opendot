"""Backup endpoints (``backup_status``, ``backup_create``, ``backup_restore``), backed by ``backup.py``.

- Backups are encrypted files in ``<database folder>/backups`` (``extras["backup_dir"]`` overrides it);
  a backup's id is its file name without the suffix. The AES key lives in the OS keychain under the
  same name the CLI uses (``backup-encryption-key``) and is created on the first backup.
- Whether a backup was verified (a restore rehearsal on a throwaway copy) is remembered in the
  ``backup_verified`` setting.
- Restore is gated by the approval flow and is never one call. The first ``POST /backup/restore``
  only proposes the restore (an ``database_restore`` approval the user decides in the Approvals
  screen) and answers 202 with ``restored: false``. After the user approves, repeating the same
  request consumes that approval's one-time token (held only in the escrow), restores, and answers
  200 with ``restored: true`` and ``restart_required``. A lost token (restart), an expired or denied
  approval, or a consumed one starts over with a new proposal.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...backup import EncryptedBackupService
from ...policy import PolicyError
from ..models import BackupCreateRequest, BackupInfo, BackupRestoreRequest, BackupRestoreResult, BackupStatus
from .config_support import error, has_secret, ok, parse_body, run, secret_store, settings_store

if TYPE_CHECKING:
    from . import ApiContext

KEY_SECRET = "backup-encryption-key"
SUFFIX = ".opendot-backup"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_VERIFIED_KEY = "backup_verified"
_PENDING_KEY = "backup_restore_pending"


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None or ctx.approvals is None:
        return []
    store = settings_store(ctx)
    service = EncryptedBackupService(ctx.database, ctx.approvals)

    def folder() -> Path:
        configured = ctx.extras.get("backup_dir")
        return Path(configured) if configured else ctx.database.path.resolve().parent / "backups"

    def path_for(backup_id: str) -> Path | None:
        if not _ID.match(backup_id):
            return None
        candidate = folder() / f"{backup_id}{SUFFIX}"
        return candidate if candidate.is_file() else None

    def info(path: Path, verified: dict[str, bool]) -> BackupInfo:
        stat = path.stat()
        return BackupInfo(
            id=path.name[: -len(SUFFIX)],
            created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            size_bytes=stat.st_size,
            verified=verified.get(path.name[: -len(SUFFIX)], False),
        )

    def listing() -> list[BackupInfo]:
        verified = store.get(_VERIFIED_KEY, dict[str, bool], {})
        files = sorted(folder().glob(f"*{SUFFIX}"), reverse=True) if folder().is_dir() else []
        return [info(path, verified) for path in files]

    def encoded_key(create: bool) -> str | None:
        secrets = secret_store(ctx)
        if not has_secret(secrets, KEY_SECRET):
            if not create:
                return None
            secrets.store(KEY_SECRET, EncryptedBackupService.generate_key())  # type: ignore[attr-defined]
        return secrets.get_required(KEY_SECRET)

    async def backup_status(_: Request) -> Response:
        def build() -> BackupStatus:
            backups = listing()
            return BackupStatus(
                backups=backups,
                last_backup_at=max((b.created_at for b in backups), default=None),
                encryption_key_present=has_secret(secret_store(ctx), KEY_SECRET),
            )

        return ok(await run(build))

    async def backup_create(request: Request) -> Response:
        body = await parse_body(request, BackupCreateRequest)
        if isinstance(body, Response):
            return body

        def create() -> BackupInfo | Response:
            try:
                key = encoded_key(create=True)
            except Exception:  # noqa: BLE001 - the keychain's message is not shown
                return error(503, "keychain_unavailable", "The OS keychain could not provide the backup key.")
            assert key is not None
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            target = folder() / f"opendot-{stamp}{SUFFIX}"
            service.create(target, encoded_key=key)
            verified = False
            if body.verify:
                verified = service.verify_restore(target, encoded_key=key).ok
            marks = store.get(_VERIFIED_KEY, dict[str, bool], {})
            marks[target.name[: -len(SUFFIX)]] = verified
            store.set(_VERIFIED_KEY, marks)
            return info(target, marks)

        result = await run(create)
        return result if isinstance(result, Response) else ok(result)

    async def backup_restore(request: Request) -> Response:
        body = await parse_body(request, BackupRestoreRequest)
        if isinstance(body, Response):
            return body
        backup = path_for(body.backup_id)
        if backup is None:
            return error(404, "not_found", "No such backup.")

        def restore() -> Response:
            key = encoded_key(create=False)
            if key is None:
                return error(409, "backup_key_missing", "The backup encryption key is not in the OS keychain.")
            pending = store.get(_PENDING_KEY, dict[str, str], {})
            approval_id = pending.get(body.backup_id)
            approval = ctx.approvals.get(approval_id) if approval_id else None
            if approval is not None and approval.state == "approved":
                token = ctx.escrow.take(approval.id)
                if token is not None:
                    try:
                        service.execute_restore(approval.id, actor=ctx.actor, token=token, encoded_key=key)
                    except (PolicyError, ValueError) as failure:
                        return error(409, "restore_failed", str(failure))
                    pending.pop(body.backup_id, None)
                    store.set(_PENDING_KEY, pending)
                    return ok(BackupRestoreResult(restored=True, backup_id=body.backup_id, restart_required=True))
                # approved, but the one-time token is gone (the daemon restarted): ask again
            if approval is None or approval.state != "pending":
                try:
                    approval = service.propose_restore(backup, actor=ctx.actor)
                except ValueError as failure:
                    return error(404, "not_found", str(failure))
                pending[body.backup_id] = approval.id
                store.set(_PENDING_KEY, pending)
            return ok(BackupRestoreResult(restored=False, backup_id=body.backup_id, restart_required=False), 202)

        return await run(restore)

    return [
        Route("/v1/backup", backup_status, methods=["GET"]),
        Route("/v1/backup", backup_create, methods=["POST"]),
        Route("/v1/backup/restore", backup_restore, methods=["POST"]),
    ]
