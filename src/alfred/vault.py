"""Optional, local Markdown projection compatible with an Obsidian vault."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .documents import DocumentStore
from .events import EventStore
from .memory_graph import Entity, GraphError, Memory, MemoryGraph, Sensitivity


class VaultError(ValueError):
    """Raised when a vault projection would be unsafe or non-portable."""


class Projection(BaseModel):
    path: Path
    conflict_copy: bool


class SourceEventProjection(BaseModel):
    source_event_id: str
    projections: list[Projection]
    skipped_memory_ids: list[str]


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

    def export_by_source_event(self, source_event_id: str, *, actor: str = "user:cli") -> SourceEventProjection:
        """Project every confirmed, vault-safe memory from one source event.

        Non-confirmed or non-exportable memories are explicitly reported as
        skipped: a bulk export must never downgrade a secret's sensitivity or
        turn a candidate into a confirmed memory.
        """
        projections: list[Projection] = []
        skipped_memory_ids: list[str] = []
        for memory in self.graph.memories_by_source_event(source_event_id):
            if memory.status != "confirmed" or self._memory_sensitivity(memory.id) not in self.allowed_sensitivities:
                skipped_memory_ids.append(memory.id)
                continue
            path = self._managed_path("Generated", "Memories", f"{memory.id}.md")
            projections.append(self._write_managed(path, self._render_memory(memory)))
        self._audit(
            actor,
            "vault_export_by_source_event",
            {
                "source_event_id": source_event_id,
                "projected_count": str(len(projections)),
                "skipped_count": str(len(skipped_memory_ids)),
            },
        )
        return SourceEventProjection(
            source_event_id=source_event_id, projections=projections, skipped_memory_ids=skipped_memory_ids
        )

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


class VaultImportResult(BaseModel):
    scanned: int
    imported: int
    updated: int
    skipped: int


class VaultImporter:
    """Read-only import: user-authored Markdown becomes confirmed, evidence-backed memory.

    This is the missing half of Section 5's vault sync, but it is a scan you
    call periodically (via the CLI, or as a connector in AlfredRunner), not
    an OS-level file watcher -- no inotify/ReadDirectoryChangesW is involved,
    so a change is only picked up on the next sync(). Each import hashes the
    note, appends a file event, and proposes (here, directly creates, since
    the owner authoring a note in their own vault already counts as an
    explicit statement) a memory from it. Alfred never writes back to an
    imported file -- identity and change detection live entirely in Alfred's
    own connector_records, keyed by the file's path relative to the vault
    root -- so importing can never overwrite user prose. Deleting a note does
    not delete the memory it produced; only the explicit `forget` command
    does that, per the doc's "does not secretly erase" rule.
    """

    connector_name = "obsidian_vault"

    def __init__(self, database: Database, vault_root: Path | str) -> None:
        self.database = database
        self.graph = MemoryGraph(database)
        self.vault_root = Path(vault_root).resolve()

    def sync(self, *, actor: str = "user:vault") -> VaultImportResult:
        """Import every changed, non-generated note; unreadable or empty notes are skipped."""
        self.database.migrate()
        account = str(self.vault_root)
        scanned = imported = updated = skipped = 0
        for path in self._eligible_files():
            scanned += 1
            outcome = self._import_one(path, account=account, actor=actor)
            if outcome == "imported":
                imported += 1
            elif outcome == "updated":
                updated += 1
            elif outcome == "skipped":
                skipped += 1
            # "unchanged" contributes only to `scanned`.
        return VaultImportResult(scanned=scanned, imported=imported, updated=updated, skipped=skipped)

    def _import_one(self, path: Path, *, account: str, actor: str) -> str:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        if frontmatter.get("managed") == "true":
            return "skipped"  # Alfred's own generated output; never re-imported as testimony.
        statement = body.strip()
        if not statement:
            return "skipped"
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        record_id = path.relative_to(self.vault_root).as_posix()
        with self.database.connect() as connection:
            previous_row = connection.execute(
                "SELECT payload_json FROM connector_records WHERE connector = ? AND account = ? AND record_type = 'note' AND record_id = ?",
                (self.connector_name, account, record_id),
            ).fetchone()
        previous = json.loads(previous_row["payload_json"]) if previous_row else None
        if previous is not None and previous.get("hash") == content_hash:
            return "unchanged"

        frontmatter_sensitivity = frontmatter.get("sensitivity")
        sensitivity: Sensitivity = (
            cast(Sensitivity, frontmatter_sensitivity)
            if frontmatter_sensitivity in {"public", "personal", "sensitive", "secret"}
            else "personal"
        )
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                event = EventStore.append(
                    connection,
                    source=self.connector_name,
                    external_id=f"{record_id}:{content_hash}",
                    occurred_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    content=statement[:200],
                    metadata={"path": record_id, "hash": content_hash},
                    sensitivity=sensitivity,
                )
                # The note itself is the raw artifact this event observed --
                # a pointer to it, never a copy of its bytes.
                DocumentStore.append(
                    connection,
                    event_id=event.id,
                    uri=str(path),
                    checksum=content_hash,
                    mime_type="text/markdown",
                    retention_policy="vault-local",
                )

        outcome = "imported"
        memory: Memory | None = None
        if previous is not None and previous.get("memory_id"):
            try:
                memory = self.graph.supersede_memory(previous["memory_id"], statement, actor=actor)
                outcome = "updated"
            except GraphError:
                memory = None  # the prior memory was forgotten; treat this as a fresh import
        if memory is None:
            memory = self.graph.remember(
                statement,
                kind="vault_note",
                source_event_id=event.id,
                sensitivity=sensitivity,
                actor=actor,
            )
            outcome = "imported"

        with self.database.connect() as connection:
            with self.database.transaction(connection):
                ConnectorRecordStore.upsert(
                    connection,
                    connector=self.connector_name,
                    account=account,
                    record_type="note",
                    record_id=record_id,
                    payload={"hash": content_hash, "memory_id": memory.id},
                    active=True,
                )
        self._audit(actor, "vault_import_note", {"path": record_id, "outcome": outcome})
        return outcome

    def _eligible_files(self) -> list[Path]:
        """Every Markdown file is scanned; ``managed: true`` frontmatter decides exclusion.

        A folder-based Generated/ exclusion would miss a managed block written
        elsewhere, and every file VaultProjector writes already carries
        ``managed: true``, so that single marker is the correct and complete
        signal -- not the folder it happens to live in.
        """
        return sorted(self.vault_root.rglob("*.md"))

    def _audit(self, actor: str, tool: str, result: dict[str, str]) -> None:
        AuditLog(self.database).append(
            AuditEvent(actor=actor, client="vault", tool=tool, outcome="ok", result=result)
        )


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Parse the simple key: value frontmatter block VaultProjector itself writes."""
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    frontmatter: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            frontmatter[key.strip()] = value.strip()
    return frontmatter, raw[end + 5 :]
