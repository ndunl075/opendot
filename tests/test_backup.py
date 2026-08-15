from pathlib import Path

import pytest

from alfred.backup import EncryptedBackupService
from alfred.db import Database
from alfred.memory_graph import MemoryGraph
from alfred.policy import ApprovalService


def test_encrypted_backup_restores_only_after_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    original = graph.remember("Before backup.")
    approvals = ApprovalService(database)
    service = EncryptedBackupService(database, approvals)
    key = service.generate_key()
    backup = tmp_path / "alfred.backup"

    created = service.create(backup, encoded_key=key)
    graph.remember("After backup.")
    proposal = service.propose_restore(backup, actor="nico")
    issued = approvals.approve(proposal.id, actor="nico")
    restored = service.execute_restore(proposal.id, actor="nico", token=issued.token, encoded_key=key)

    assert created.sha256 == restored.backup_sha256
    assert graph.get_memory(original.id).statement == "Before backup."
    assert [memory.statement for memory in graph.search("After backup").memories] == []


def test_backup_restore_rejects_changed_file(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    MemoryGraph(database).remember("Backup content.")
    approvals = ApprovalService(database)
    service = EncryptedBackupService(database, approvals)
    backup = tmp_path / "alfred.backup"
    key = service.generate_key()
    service.create(backup, encoded_key=key)
    proposal = service.propose_restore(backup, actor="nico")
    backup.write_bytes(backup.read_bytes() + b"changed")
    issued = approvals.approve(proposal.id, actor="nico")
    with pytest.raises(ValueError, match="changed after"):
        service.execute_restore(proposal.id, actor="nico", token=issued.token, encoded_key=key)


def _service(tmp_path: Path) -> tuple[EncryptedBackupService, Database]:
    database = Database(tmp_path / "alfred.db")
    return EncryptedBackupService(database, ApprovalService(database)), database


def test_a_drill_confirms_the_data_is_really_there(tmp_path: Path) -> None:
    """A backup that decrypts and passes an integrity check is still worthless
    if it restored an empty database, so the drill reports row counts."""
    service, database = _service(tmp_path)
    MemoryGraph(database).remember("Something worth recovering.")
    key = EncryptedBackupService.generate_key()
    backup = tmp_path / "snapshot.alfred-backup"
    service.create(backup, encoded_key=key)

    report = service.verify_restore(backup, encoded_key=key)

    assert report.ok is True
    assert report.failure is None
    assert report.audit_chain_verified is True
    assert report.schema_version > 0
    assert report.row_counts["memories"] >= 1


def test_a_drill_never_touches_the_live_database(tmp_path: Path) -> None:
    """The reason this exists: the only other way to test a restore overwrites
    the live database, which is why nobody ever tests one."""
    service, database = _service(tmp_path)
    key = EncryptedBackupService.generate_key()
    backup = tmp_path / "snapshot.alfred-backup"
    service.create(backup, encoded_key=key)
    # Diverge the live database *after* the snapshot was taken, so a restore
    # would visibly roll this back.
    MemoryGraph(database).remember("Written after the backup.")

    service.verify_restore(backup, encoded_key=key)

    # Asserted on content, not file bytes: closing a connection checkpoints
    # the WAL into the main file, which rewrites header counters without any
    # data changing. The invariant that matters is that the live data was not
    # rolled back to the snapshot.
    assert MemoryGraph(database).search("Written after").memories


def test_a_drill_reports_a_wrong_key_instead_of_raising(tmp_path: Path) -> None:
    """Safe to run on a schedule: a broken backup is a report saying so, not a
    crashed job."""
    service, _ = _service(tmp_path)
    backup = tmp_path / "snapshot.alfred-backup"
    service.create(backup, encoded_key=EncryptedBackupService.generate_key())

    report = service.verify_restore(backup, encoded_key=EncryptedBackupService.generate_key())

    assert report.ok is False
    assert "cannot be decrypted" in report.failure


def test_a_drill_reports_a_corrupt_backup(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    key = EncryptedBackupService.generate_key()
    backup = tmp_path / "snapshot.alfred-backup"
    service.create(backup, encoded_key=key)
    backup.write_bytes(backup.read_bytes()[:2048])  # truncated mid-transfer

    report = service.verify_restore(backup, encoded_key=key)

    assert report.ok is False
    assert report.failure


def test_a_drill_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    """Windows keeps a database file locked until its connection is collected,
    so cleanup here is explicit rather than left to the interpreter."""
    import tempfile

    service, _ = _service(tmp_path)
    key = EncryptedBackupService.generate_key()
    backup = tmp_path / "snapshot.alfred-backup"
    service.create(backup, encoded_key=key)
    root = Path(tempfile.gettempdir())
    before = set(root.glob("alfred-drill-*"))

    service.verify_restore(backup, encoded_key=key)

    assert set(root.glob("alfred-drill-*")) == before


def test_latest_backup_picks_the_newest_by_timestamped_name(tmp_path: Path) -> None:
    from alfred.backup import latest_backup

    for name in ("alfred-20260101-000000", "alfred-20260814-023001", "alfred-20260501-120000"):
        (tmp_path / f"{name}.alfred-backup").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"ignored")

    assert latest_backup(tmp_path).name == "alfred-20260814-023001.alfred-backup"


def test_latest_backup_is_explicit_when_there_is_nothing_to_verify(tmp_path: Path) -> None:
    from alfred.backup import latest_backup

    with pytest.raises(ValueError, match="no .alfred-backup files"):
        latest_backup(tmp_path)
