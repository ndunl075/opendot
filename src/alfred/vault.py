"""Optional, local Markdown projection compatible with an Obsidian vault."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .memory_graph import Entity, GraphError, Memory, MemoryGraph


class VaultError(ValueError):
    """Raised when a vault projection would be unsafe or non-portable."""


class Projection(BaseModel):
    path: Path
    conflict_copy: bool


class VaultProjector:
    """Write selected graph records as safe, portable local Markdown files."""

    allowed_sensitivities = {"public", "personal"}

    def __init__(self, database: Database, vault_root: Path | str) -> None:
        self.database = database
        self.graph = MemoryGraph(database)
        self.vault_root = Path(vault_root).resolve()

    def project_entity(self, entity_id: str, *, actor: str = "user:cli") -> Projection:
        entity = self.graph.get_entity(entity_id)
        if entity is None:
            raise VaultError(f"entity does not exist: {entity_id}")
        self._assert_exportable(entity.sensitivity)
        path = self._managed_path("Generated", "Entities", f"{entity.id}.md")
        projection = self._write_managed(path, self._render_entity(entity))
        self._audit(actor, "vault_project_entity", {"entity_id": entity.id, "path": str(projection.path)})
        return projection

    def project_memory(self, memory_id: str, *, actor: str = "user:cli") -> Projection:
        memory = self.graph.get_memory(memory_id)
        if memory is None:
            raise VaultError(f"memory does not exist: {memory_id}")
        if memory.status != "confirmed":
            raise VaultError("only confirmed memories can be projected to the vault")
        self._assert_exportable(self._memory_sensitivity(memory_id))
        path = self._managed_path("Generated", "Memories", f"{memory.id}.md")
        projection = self._write_managed(path, self._render_memory(memory))
        self._audit(actor, "vault_project_memory", {"memory_id": memory.id, "path": str(projection.path)})
        return projection

    def _memory_sensitivity(self, memory_id: str) -> str:
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT sensitivity FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise VaultError(f"memory does not exist: {memory_id}")
        return row["sensitivity"]

    def _managed_path(self, *parts: str) -> Path:
        candidate = self.vault_root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(self.vault_root):
            raise VaultError("vault projection path escaped the vault root")
        return candidate

    def _write_managed(self, path: Path, contents: str) -> Projection:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not self._is_managed(path):
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            conflict = path.with_name(f"{path.stem}.alfred-conflict-{timestamp}{path.suffix}")
            conflict.write_text(contents, encoding="utf-8", newline="\n")
            return Projection(path=conflict, conflict_copy=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
        return Projection(path=path, conflict_copy=False)

    @staticmethod
    def _is_managed(path: Path) -> bool:
        return "managed: true" in path.read_text(encoding="utf-8")[:1024]

    def _assert_exportable(self, sensitivity: str) -> None:
        if sensitivity not in self.allowed_sensitivities:
            raise VaultError(f"{sensitivity} graph data is not exported to the vault")

    def _audit(self, actor: str, tool: str, result: dict[str, str]) -> None:
        AuditLog(self.database).append(
            AuditEvent(actor=actor, client="vault", tool=tool, outcome="ok", result=result)
        )

    @staticmethod
    def _render_entity(entity: Entity) -> str:
        properties = json.dumps(entity.properties, indent=2, sort_keys=True, ensure_ascii=False)
        domains = ", ".join(entity.domains) if entity.domains else "none"
        return (
            "---\n"
            f"alfred_id: {entity.id}\n"
            f"type: {entity.entity_type}\n"
            f"sensitivity: {entity.sensitivity}\n"
            "managed: true\n"
            f"generated_at: {datetime.now(UTC).isoformat()}\n"
            "---\n\n"
            f"# {entity.label}\n\n"
            f"Domains: {domains}\n\n"
            "## Properties\n\n"
            f"```json\n{properties}\n```\n"
        )

    @staticmethod
    def _render_memory(memory: Memory) -> str:
        slug = re.sub(r"\s+", " ", memory.kind).strip() or "memory"
        return (
            "---\n"
            f"alfred_id: {memory.id}\n"
            f"type: memory\n"
            f"kind: {slug}\n"
            "managed: true\n"
            f"generated_at: {datetime.now(UTC).isoformat()}\n"
            "---\n\n"
            f"# {slug.title()}\n\n"
            f"{memory.statement}\n"
        )
