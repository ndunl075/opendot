from pathlib import Path

import pytest

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.memory_graph import MemoryGraph
from alfred.vault import VaultError, VaultProjector


def test_projected_entity_is_plain_markdown_with_stable_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred", properties={"stage": "MVP"})
    vault = tmp_path / "vault"

    projection = VaultProjector(database, vault).project_entity(entity.id)

    assert projection.conflict_copy is False
    contents = projection.path.read_text(encoding="utf-8")
    assert f"alfred_id: {entity.id}" in contents
    assert "managed: true" in contents
    assert "# Alfred" in contents
    assert "\"stage\": \"MVP\"" in contents
    assert AuditLog(database).verify() is True


def test_sensitive_graph_records_are_not_exported(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="person", label="Private", sensitivity="sensitive")

    with pytest.raises(VaultError, match="not exported"):
        VaultProjector(database, tmp_path / "vault").project_entity(entity.id)


def test_manual_file_is_preserved_with_a_conflict_copy(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred")
    vault = tmp_path / "vault"
    path = vault / "Generated" / "Entities" / f"{entity.id}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# My manual note\n", encoding="utf-8")

    projection = VaultProjector(database, vault).project_entity(entity.id)

    assert projection.conflict_copy is True
    assert path.read_text(encoding="utf-8") == "# My manual note\n"
    assert projection.path.name.startswith(f"{entity.id}.alfred-conflict-")
    assert "managed: true" in projection.path.read_text(encoding="utf-8")


def test_only_confirmed_memories_are_projected(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    candidate = graph.remember("Possibly likes tea.", status="candidate", confidence=0.3, confirmed=False)
    confirmed = graph.remember("Likes coffee.")
    projector = VaultProjector(database, tmp_path / "vault")

    with pytest.raises(VaultError, match="only confirmed"):
        projector.project_memory(candidate.id)
    projection = projector.project_memory(confirmed.id)

    assert "Likes coffee." in projection.path.read_text(encoding="utf-8")
