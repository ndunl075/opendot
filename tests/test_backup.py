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
