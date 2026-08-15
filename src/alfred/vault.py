"""Optional, local Markdown projection compatible with an Obsidian vault."""

from __future__ import annotations

import hashlib
import json
import os
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


#: Windows rejects any path at or over MAX_PATH unless the process opts into
#: long paths (off by default) or the path carries the ``\\?\`` extended-length
#: prefix. 260 is the documented limit including the terminating null, so a
#: path is already unsafe at 260 characters.
_WINDOWS_MAX_PATH = 259


def _fs(path: Path) -> Path:
    """Return a form of ``path`` Windows can actually open past MAX_PATH.

    Alfred generates its own vault filenames -- a 36-character UUID plus, for
    a conflict copy, a 33-character ``.alfred-conflict-<timestamp>`` suffix --
    so a deeply nested vault root can push an otherwise ordinary export over
    the limit. Without this, that surfaces as a bare ``FileNotFoundError: No
    such file or directory``, which names the wrong problem entirely and sends
    you looking for a missing directory that is right there.

    Applied only at the I/O boundary. The prefix is a Win32 filename
    convention, not part of the path's identity, so ``Projection.path``, audit
    records, and connector record IDs all keep the plain form. No-op on every
    other platform, and on paths that are already short or already prefixed.
    """
    if os.name != "nt":
        return path
    text = str(path)
    if len(text) <= _WINDOWS_MAX_PATH or text.startswith("\\\\?\\"):
        return path
    # A UNC path takes \\?\UNC\server\share, not \\?\\\server\share.
    if text.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{text[2:]}")
    return Path(f"\\\\?\\{text}")


class VaultError(ValueError):
    """Raised when a vault projection would be unsafe or non-portable."""


class Projection(BaseModel):
    path: Path
    conflict_copy: bool


class SourceEventProjection(BaseModel):
    source_event_id: str
    projections: list[Projection]
    skipped_memory_ids: list[str]


class SelectionProjection(BaseModel):
    """Result of a bulk export whose scope is a query rather than one record.

    ``selector`` names how the set was chosen so a receipt stays inspectable
    after the fact; the query text itself is deliberately not stored here,
    since a topic search can contain exactly the private phrasing the vault's
    sensitivity rules exist to keep out of a generated file.
    """

    selector: str
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
        projections, skipped_memory_ids = self._project_all(
            self.graph.memories_by_source_event(source_event_id)
        )
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

    def export_by_time_range(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        actor: str = "user:cli",
    ) -> SelectionProjection:
        """Project confirmed, vault-safe memories recorded in ``[since, until)``.

        Section 4's "users can export or delete by source, time range, person,
        topic, or individual item" -- this is the time-range selector. Same
        skip rules as every other bulk export: a wider net must not quietly
        widen what is exportable.
        """
        memories = self.graph.memories_in_range(since=since, until=until)
        projections, skipped_memory_ids = self._project_all(memories)
        self._audit(
            actor,
            "vault_export_by_time_range",
            {
                "since": since.isoformat() if since else "",
                "until": until.isoformat() if until else "",
                "projected_count": str(len(projections)),
                "skipped_count": str(len(skipped_memory_ids)),
            },
        )
        return SelectionProjection(
            selector="time_range",
            projections=projections,
            skipped_memory_ids=skipped_memory_ids,
        )

    def export_by_topic(self, query: str, *, limit: int = 50, actor: str = "user:cli") -> SelectionProjection:
        """Project confirmed, vault-safe memories matching a topic search.

        Uses the same retrieval path a question would, so what you export is
        what Alfred would actually recall -- deliberately not a second,
        divergent matching rule. Sensitivity is restricted to the vault-safe
        set at query time as well as at projection time, so a `secret` match
        never even enters the candidate list.
        """
        result = self.graph.search(
            query, limit=limit, allowed_sensitivities=set(self.allowed_sensitivities)
        )
        projections, skipped_memory_ids = self._project_all(result.memories)
        self._audit(
            actor,
            "vault_export_by_topic",
            {
                "projected_count": str(len(projections)),
                "skipped_count": str(len(skipped_memory_ids)),
            },
        )
        return SelectionProjection(
            selector="topic",
            projections=projections,
            skipped_memory_ids=skipped_memory_ids,
        )

    def _project_all(self, memories: list[Memory]) -> tuple[list[Projection], list[str]]:
        """Project each vault-safe confirmed memory; report the rest as skipped.

        Shared by every bulk selector so they cannot drift apart on the one
        rule that matters: a bulk export must never downgrade a secret's
        sensitivity or turn a candidate into a confirmed memory.
        """
        projections: list[Projection] = []
        skipped_memory_ids: list[str] = []
        for memory in memories:
            if memory.status != "confirmed" or self._memory_sensitivity(memory.id) not in self.allowed_sensitivities:
                skipped_memory_ids.append(memory.id)
                continue
            path = self._managed_path("Generated", "Memories", f"{memory.id}.md")
            projections.append(self._write_managed(path, self._render_memory(memory)))
        return projections, skipped_memory_ids

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
        _fs(path.parent).mkdir(parents=True, exist_ok=True)
        if _fs(path).exists() and not self._is_managed(path):
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            conflict = path.with_name(f"{path.stem}.alfred-conflict-{timestamp}{path.suffix}")
            _fs(conflict).write_text(contents, encoding="utf-8", newline="\n")
            return Projection(path=conflict, conflict_copy=True)
        _fs(path).write_text(contents, encoding="utf-8", newline="\n")
        return Projection(path=path, conflict_copy=False)

    @staticmethod
    def _is_managed(path: Path) -> bool:
        return "managed: true" in _fs(path).read_text(encoding="utf-8")[:1024]

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


#: Obsidian's link syntax, covering the forms that name a note: ``[[Note]]``,
#: ``[[Note|shown text]]``, and ``[[Note#Heading]]``. Only the target matters
#: here -- the display text is presentation and the heading is a location
#: inside the target, neither of which changes which note is referenced.
#: Embeds (``![[Note]]``) are matched too; an embed is still a reference.
_WIKI_LINK = re.compile(r"\[\[\s*([^\[\]|#\n]+?)\s*(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")


def wiki_link_targets(text: str) -> list[str]:
    """Return each distinct note name a body links to, in first-seen order.

    Deduplicated because linking the same person three times in one note is
    emphasis, not three separate pieces of evidence.
    """
    seen: dict[str, None] = {}
    for match in _WIKI_LINK.finditer(text):
        target = " ".join(match.group(1).split())
        if target:
            seen.setdefault(target, None)
    return list(seen)


class VaultImportResult(BaseModel):
    scanned: int
    imported: int
    updated: int
    skipped: int
    #: Wiki links resolved to exactly one existing entity and recorded as
    #: provenance. Links naming nothing Alfred knows, or naming something
    #: ambiguous, are counted separately rather than guessed at.
    linked: int = 0
    unresolved_links: int = 0


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
        scanned = imported = updated = skipped = linked = unresolved = 0
        for path in self._eligible_files():
            scanned += 1
            outcome, link_counts = self._import_one(path, account=account, actor=actor)
            if outcome == "imported":
                imported += 1
            elif outcome == "updated":
                updated += 1
            elif outcome == "skipped":
                skipped += 1
            # "unchanged" contributes only to `scanned`.
            linked += link_counts[0]
            unresolved += link_counts[1]
        return VaultImportResult(
            scanned=scanned,
            imported=imported,
            updated=updated,
            skipped=skipped,
            linked=linked,
            unresolved_links=unresolved,
        )

    def _import_one(self, path: Path, *, account: str, actor: str) -> tuple[str, tuple[int, int]]:
        """Return the outcome plus (resolved links, unresolved links)."""
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        if frontmatter.get("managed") == "true":
            # Alfred's own generated output; never re-imported as testimony,
            # and its links are Alfred's own writing rather than the owner's.
            return "skipped", (0, 0)
        statement = body.strip()
        if not statement:
            return "skipped", (0, 0)
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        record_id = path.relative_to(self.vault_root).as_posix()
        with self.database.connect() as connection:
            previous_row = connection.execute(
                "SELECT payload_json FROM connector_records WHERE connector = ? AND account = ? AND record_type = 'note' AND record_id = ?",
                (self.connector_name, account, record_id),
            ).fetchone()
        previous = json.loads(previous_row["payload_json"]) if previous_row else None
        if previous is not None and previous.get("hash") == content_hash:
            return "unchanged", (0, 0)

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
        self._ensure_declared_entity(frontmatter, path, source_event_id=event.id, actor=actor)
        link_counts = self._record_links(body, source_event_id=event.id, actor=actor)
        self._audit(
            actor,
            "vault_import_note",
            {
                "path": record_id,
                "outcome": outcome,
                "linked": str(link_counts[0]),
                "unresolved_links": str(link_counts[1]),
            },
        )
        return outcome, link_counts

    def _ensure_declared_entity(
        self,
        frontmatter: dict[str, str],
        path: Path,
        *,
        source_event_id: str,
        actor: str,
    ) -> None:
        """Create the entity a note declares itself to be, if it has no ID yet.

        A note whose frontmatter says ``type: person`` is the owner stating
        that this person exists -- an explicit statement, so the entity is
        created ``confirmed``, unlike the unconfirmed ones derived from
        connector data. The note's filename is the label, which is also what
        makes ``[[Alex Chen]]`` elsewhere resolve to it.

        Only registry types are accepted and an existing ``alfred_id`` is left
        alone: a projected note already has its entity, and re-creating one
        from Alfred's own output would duplicate it.
        """
        declared = (frontmatter.get("type") or "").strip().lower()
        if not declared or frontmatter.get("alfred_id"):
            return
        with self.database.connect() as connection:
            allowed = connection.execute(
                "SELECT 1 FROM type_registry WHERE name = ? AND enabled = 1 AND confirmed = 1",
                (declared,),
            ).fetchone()
        # "self" is the single owner node, created once by `alfred memory-self`;
        # a note must not mint a second one.
        if allowed is None or declared == "self":
            return
        label = path.stem.strip()
        if not label or self.graph.resolve_entity_by_name(label) is not None:
            return
        self.graph.create_entity(
            entity_type=declared,
            label=label,
            sensitivity="personal",
            source_event_id=source_event_id,
            actor=actor,
        )

    def _record_links(self, body: str, *, source_event_id: str, actor: str) -> tuple[int, int]:
        """Record each ``[[wiki link]]`` that names exactly one known entity.

        Section 5 says the importer parses frontmatter *and links*, and that
        wiki links can express relationships. This is the conservative half of
        that: a link the owner typed is an explicit statement that this note
        concerns that entity, so it is recorded as provenance against the
        note's source event.

        What it deliberately does not do is decide what the link *means*.
        Turning "[[Alex]]" into a typed edge would require inventing a
        predicate, and this section requires relationships to be typed,
        registry-validated, and temporal. It also never creates an entity: a
        link to a note Alfred has never heard of is counted as unresolved
        rather than promoted into the graph, since a filename is not evidence
        that a thing exists. Ambiguous names resolve to nothing at all, per
        the rule that two possible "Alex" entities beat one wrong merge.
        """
        linked = unresolved = 0
        for target in wiki_link_targets(body):
            entity = self.graph.resolve_entity_by_name(target)
            if entity is None:
                unresolved += 1
                continue
            self.graph.record_entity_mention(
                entity.id, source_event_id=source_event_id, excerpt=target, actor=actor
            )
            linked += 1
        return linked, unresolved

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
