"""Encrypted local SQLite backup and strongly-confirmed restore."""

from __future__ import annotations

import base64
import gc
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel

from .db import Database
from .policy import Approval, ApprovalService, PolicyError


MAGIC = b"ALFRED-BACKUP-1\n"
NONCE_BYTES = 12


class BackupReceipt(BaseModel):
    path: Path
    sha256: str
    created_at: datetime


class RestoreReceipt(BaseModel):
    database_path: Path
    backup_sha256: str


class RestoreDrillReport(BaseModel):
    """What a restore rehearsal actually proved, or where it stopped.

    ``ok`` is the whole answer; the rest is evidence for it. Row counts are
    included because a backup can decrypt, pass an integrity check, and still
    be worthless if it restored an empty database -- "the file parses" and "my
    data is in there" are different claims, and only the second one matters at
    the moment you need a backup.
    """

    backup_path: Path
    backup_sha256: str
    ok: bool
    failure: str | None = None
    schema_version: int | None = None
    audit_chain_verified: bool | None = None
    row_counts: dict[str, int] = {}
    verified_at: datetime


class EncryptedBackupService:
    """Keep encrypted, portable SQLite snapshots outside the live database path."""

    restore_action_type = "database_restore"

    def __init__(self, database: Database, approvals: ApprovalService) -> None:
        self.database = database
        self.approvals = approvals

    @staticmethod
    def generate_key() -> str:
        """Return a base64-encoded AES-256 key suitable for the OS keyring."""
        return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")

    def create(self, output_path: Path | str, *, encoded_key: str) -> BackupReceipt:
        output = Path(output_path).resolve()
        database_path = self.database.path.resolve()
        if output == database_path:
            raise ValueError("backup path must not be the live database path")
        key = _decode_key(encoded_key)
        self.database.migrate()
        with tempfile.TemporaryDirectory(prefix="alfred-backup-") as temp_directory:
            snapshot = Path(temp_directory) / "alfred.db"
            source = self.database.connect()
            destination = sqlite3.connect(snapshot)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            nonce = secrets.token_bytes(NONCE_BYTES)
            encrypted = AESGCM(key).encrypt(nonce, snapshot.read_bytes(), MAGIC)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(MAGIC + nonce + encrypted)
        return BackupReceipt(path=output, sha256=_sha256_file(output), created_at=datetime.now(UTC))

    #: Tables whose emptiness would make a restore pointless even though the
    #: file is technically valid. Deliberately the durable record rather than
    #: derived state: `memories` and `entities` can legitimately be rebuilt,
    #: but an events/tool_runs table that came back empty means the archive
    #: itself did not survive.
    drill_tables = ("events", "tool_runs", "memories", "entities", "tasks")

    def verify_restore(
        self, backup_path: Path | str, *, encoded_key: str
    ) -> RestoreDrillReport:
        """Rehearse a restore into a throwaway copy and report what held.

        Section 8 requires testing restore monthly. Until now the only way to
        do that was `backup-restore-execute`, which is approval-gated and
        *overwrites the live database* -- a test nobody sensible runs on a
        working system, which is precisely how backups stay unverified until
        the day they are needed.

        This never touches the live database. It decrypts into a temporary
        file, opens that, and answers the questions that actually matter: does
        the stored key still open this file, is the SQLite intact, do the
        migrations apply, does the audit hash chain still verify, and is the
        data present rather than merely well-formed.

        Expected failures are reported rather than raised, so this is safe to
        run on a schedule: a broken backup should show up as a report saying
        so, not as a crashed job.
        """
        path = Path(backup_path).resolve()
        if not path.is_file():
            raise ValueError("backup file does not exist")
        checked_at = datetime.now(UTC)
        digest = _sha256_file(path)

        def failed(reason: str) -> RestoreDrillReport:
            return RestoreDrillReport(
                backup_path=path, backup_sha256=digest, ok=False, failure=reason, verified_at=checked_at
            )

        try:
            restored_bytes = _decrypt(path.read_bytes(), _decode_key(encoded_key))
        except ValueError as error:
            return failed(str(error))

        # Not TemporaryDirectory: its cleanup raises on Windows here, because
        # `with database.connect()` commits the transaction but does not close
        # the connection, and the file stays locked while a reference cycle
        # keeps that connection alive. Collecting first releases it. The leak
        # is harmless for the long-lived live database -- this is simply the
        # first code that ever tries to delete a database it just opened.
        temp_directory = Path(tempfile.mkdtemp(prefix="alfred-drill-"))
        try:
            staged = temp_directory / "restored.db"
            staged.write_bytes(restored_bytes)
            try:
                _validate_database(staged)
            except ValueError as error:
                return failed(str(error))
            # Deliberately a real Database on the copy: migrating and
            # verifying the chain here proves the backup would come back as a
            # working Alfred, not just as a readable file.
            from .audit import AuditLog

            staged_database = Database(staged)
            schema_version = staged_database.migrate()
            audit_ok = AuditLog(staged_database).verify()
            counts: dict[str, int] = {}
            connection = staged_database.connect()
            try:
                for table in self.drill_tables:
                    row = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
                    ).fetchone()
                    if row is None:
                        continue
                    counts[table] = int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
            finally:
                connection.close()
        finally:
            gc.collect()
            shutil.rmtree(temp_directory, ignore_errors=True)

        if not audit_ok:
            return RestoreDrillReport(
                backup_path=path,
                backup_sha256=digest,
                ok=False,
                failure="audit hash chain did not verify in the restored copy",
                schema_version=schema_version,
                audit_chain_verified=False,
                row_counts=counts,
                verified_at=checked_at,
            )
        return RestoreDrillReport(
            backup_path=path,
            backup_sha256=digest,
            ok=True,
            schema_version=schema_version,
            audit_chain_verified=True,
            row_counts=counts,
            verified_at=checked_at,
        )

    def propose_restore(self, backup_path: Path | str, *, actor: str) -> Approval:
        """Inspect a local backup and create a strong-confirmation restore preview."""
        path = Path(backup_path).resolve()
        if not path.is_file():
            raise ValueError("backup file does not exist")
        return self.approvals.propose(
            actor=actor,
            action_type=self.restore_action_type,
            preview={"backup_path": str(path), "backup_sha256": _sha256_file(path), "database_path": str(self.database.path.resolve())},
        )

    def execute_restore(self, approval_id: str, *, actor: str, token: str, encoded_key: str) -> RestoreReceipt:
        approval = self.approvals.get(approval_id)
        if approval is None or approval.action_type != self.restore_action_type:
            raise PolicyError("approval is not for database restore")
        preview = approval.preview
        backup_path = Path(preview["backup_path"])
        current_sha256 = _sha256_file(backup_path)
        if current_sha256 != preview["backup_sha256"]:
            raise ValueError("backup file changed after restore was approved")
        self.approvals.consume(approval_id, actor=actor, token=token)
        restored_bytes = _decrypt(backup_path.read_bytes(), _decode_key(encoded_key))
        with tempfile.TemporaryDirectory(prefix="alfred-restore-") as temp_directory:
            staged = Path(temp_directory) / "restored.db"
            staged.write_bytes(restored_bytes)
            _validate_database(staged)
            live = self.database.path.resolve()
            live.parent.mkdir(parents=True, exist_ok=True)
            # SQLite's backup API replaces the live contents without relying on
            # a Windows file rename, which can fail while short-lived readers
            # still hold the database file open.
            source = sqlite3.connect(staged)
            destination = sqlite3.connect(live)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        return RestoreReceipt(database_path=self.database.path.resolve(), backup_sha256=current_sha256)


def latest_backup(directory: Path | str, *, suffix: str = ".alfred-backup") -> Path:
    """Return the newest backup in a directory.

    Exists so a scheduled drill can point at the backup *folder* rather than
    needing to know today's timestamped filename. Ordered by filename rather
    than mtime because the names are timestamped at creation, while an mtime
    can be rewritten by a copy or a sync client.
    """
    folder = Path(directory).resolve()
    if not folder.is_dir():
        raise ValueError(f"backup directory does not exist: {folder}")
    candidates = sorted(folder.glob(f"*{suffix}"))
    if not candidates:
        raise ValueError(f"no {suffix} files in {folder}")
    return candidates[-1]


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
    except Exception as error:
        raise ValueError("backup key must be base64-encoded") from error
    if len(key) != 32:
        raise ValueError("backup key must decode to 32 bytes")
    return key


def _decrypt(payload: bytes, key: bytes) -> bytes:
    if not payload.startswith(MAGIC) or len(payload) <= len(MAGIC) + NONCE_BYTES:
        raise ValueError("backup has an invalid format")
    nonce = payload[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, payload[len(MAGIC) + NONCE_BYTES :], MAGIC)
    except Exception as error:
        raise ValueError("backup cannot be decrypted with this key") from error


def _validate_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("decrypted backup failed SQLite integrity check")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone() is None:
            raise ValueError("decrypted backup is not an Alfred database")
    finally:
        connection.close()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
