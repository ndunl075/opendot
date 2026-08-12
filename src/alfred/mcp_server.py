"""Narrow stdio MCP surface, growing toward ARCHITECTURE.md section 7."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from .briefing import BriefingService
from .config import Settings
from .connector_health import connector_health
from .db import Database
from .events import EventStore
from .memory_graph import GraphError, MemoryActions, MemoryGraph, Sensitivity
from .policy import ApprovalService, PolicyError, PolicyStore
from .reminders import ReminderStore
from .tasks import UNSET, TaskStore

ALLOWED_SENSITIVITIES: frozenset[str] = frozenset({"public", "personal", "sensitive", "secret"})


def create_server(database_path: Path | str | None = None, *, client_id: str = "local-mcp") -> FastMCP:
    """Create Alfred's MCP server: local memory reads/writes and connector status.

    Every tool is gated by PolicyStore, so an unregistered or narrowly scoped
    client gets nothing by default. Deleting data is itself consequential
    (decision 8: strong-confirm, never unattended), so forget() only
    previews a deletion; action_commit() performs whatever it previewed
    after a fresh approval token is presented. action_commit currently only
    knows how to finish a memory_forget -- wiring a live Google credential
    into this stateless MCP process for the calendar write needs its own
    pass, so that action stays CLI-only (calendar-event-execute) for now.
    """
    settings = Settings.from_environment(Path(database_path) if database_path else None)
    database = Database(settings.database_path)
    policy = PolicyStore(database)
    approvals = ApprovalService(database)
    server = FastMCP("Alfred")

    @server.tool()
    def system_status() -> dict[str, int | str]:
        """Return Alfred's non-sensitive local health and schema status."""
        return database.status()

    @server.tool()
    def agenda_get() -> str:
        """Return Alfred's deterministic local task agenda with freshness."""
        return BriefingService(database).morning_brief().render()

    @server.tool()
    def memory_search(query: str) -> dict:
        """Search local memory anchors and their one-hop active graph context."""
        scope = policy.require_read(client_id, "memory_search")
        return MemoryGraph(database).search(query, allowed_sensitivities=scope.allowed_sensitivities).model_dump(mode="json")

    @server.tool()
    def profile_get() -> dict:
        """Return the local owner node and current, evidence-backed profile relationships."""
        scope = policy.require_read(client_id, "profile_get")
        owner, relationships = MemoryGraph(database).profile(allowed_sensitivities=scope.allowed_sensitivities)
        return {
            "owner": owner.model_dump(mode="json") if owner else None,
            "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
        }

    @server.tool()
    def remember(statement: str, kind: str = "note", sensitivity: str = "personal") -> dict:
        """Store a confirmed local memory; the calling client is recorded as actor."""
        if sensitivity not in ALLOWED_SENSITIVITIES:
            raise ValueError(f"unknown sensitivity: {sensitivity}")
        scope = policy.require_write(client_id, "remember")
        if sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to write sensitivity: {sensitivity}")
        memory = MemoryGraph(database).remember(
            statement, kind=kind, sensitivity=cast(Sensitivity, sensitivity), actor=f"mcp:{client_id}"
        )
        return memory.model_dump(mode="json")

    @server.tool()
    def forget(memory_id: str, reason: str = "user requested deletion") -> dict:
        """Preview deleting one memory; nothing is deleted until action_commit confirms it.

        Decision 8 classifies deleting data as strong-confirm, never
        unattended, so an MCP client -- which can call tools without a human
        watching in the moment -- cannot delete in a single call.
        """
        scope = policy.require_write(client_id, "forget")
        graph = MemoryGraph(database)
        existing = graph.get_memory(memory_id)
        if existing is None:
            raise GraphError(f"memory does not exist: {memory_id}")
        if existing.sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to forget sensitivity: {existing.sensitivity}")
        approval = MemoryActions(database, approvals).propose_forget(memory_id, actor=f"mcp:{client_id}", reason=reason)
        return approval.model_dump(mode="json")

    @server.tool()
    def action_commit(approval_id: str, token: str) -> dict:
        """Consume a fresh approval token and perform the action it previewed."""
        policy.require_write(client_id, "action_commit")
        actor = f"mcp:{client_id}"
        approval = approvals.get(approval_id)
        if approval is None:
            raise PolicyError("approval does not exist")
        if approval.action_type == "memory_forget":
            receipt = MemoryActions(database, approvals).execute_forget(approval_id, actor=actor, token=token)
            return receipt.model_dump(mode="json")
        raise PolicyError(f"action_commit does not yet support this action type: {approval.action_type}")

    @server.tool()
    def brief_get(now: str | None = None) -> str:
        """Render the deterministic local morning brief on demand, not just on schedule."""
        policy.require_read(client_id, "brief_get")
        parsed = datetime.fromisoformat(now) if now else None
        return BriefingService(database).morning_brief(parsed).render()

    @server.tool()
    def connector_status() -> list[dict]:
        """Report each connector's health; never its credentials or synced content."""
        policy.require_read(client_id, "connector_status")
        return [health.model_dump(mode="json") for health in connector_health(database)]

    @server.tool()
    def task_upsert(title: str, task_id: str | None = None, due_at: str | None = None) -> dict:
        """Create a task, or update an existing one's title/due date when task_id is given.

        Decision 8 classifies this as automatic and reversible, unlike
        deletion, so it needs no approval step.
        """
        policy.require_write(client_id, "task_upsert")
        # due_at omitted means "leave unchanged" on update ("no due date" on
        # create); the tool has no separate way to explicitly clear one.
        parsed_due = datetime.fromisoformat(due_at) if due_at else UNSET
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if task_id is None:
                    event = EventStore.append(
                        connection,
                        source="mcp",
                        external_id=f"task:{client_id}:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=title,
                        metadata={"client": client_id},
                    )
                    task = TaskStore.upsert(connection, title=title, due_at=parsed_due, source_event_id=event.id)
                else:
                    task = TaskStore.upsert(connection, task_id=task_id, title=title, due_at=parsed_due)
        return task.model_dump(mode="json")

    @server.tool()
    def task_complete(task_id: str) -> dict:
        """Mark an open task completed; completing an already-completed task is a no-op."""
        policy.require_write(client_id, "task_complete")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                task = TaskStore.complete(connection, task_id)
        return task.model_dump(mode="json")

    @server.tool()
    def reminder_set(text: str, run_at: str, chat_id: int, task_id: str | None = None) -> dict:
        """Schedule a Telegram reminder; chat_id must already be locally paired to receive it.

        Alfred's only delivery channel today is Telegram, so the caller must
        say which paired chat this goes to -- there is no channel-agnostic
        queue to defer that choice to.
        """
        policy.require_write(client_id, "reminder_set")
        parsed_run_at = datetime.fromisoformat(run_at)
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if task_id is None:
                    event = EventStore.append(
                        connection,
                        source="mcp",
                        external_id=f"reminder:{client_id}:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=text,
                        metadata={"client": client_id},
                    )
                    task = TaskStore.upsert(connection, title=text, due_at=parsed_run_at, source_event_id=event.id)
                    resolved_task_id = task.id
                else:
                    resolved_task_id = task_id
                job = ReminderStore.create(
                    connection,
                    run_at=parsed_run_at,
                    task_id=resolved_task_id,
                    chat_id=chat_id,
                    text=text,
                    idempotency_key=f"mcp-reminder:{client_id}:{resolved_task_id}:{parsed_run_at.isoformat()}",
                )
        return job.model_dump(mode="json")

    return server


def main() -> None:
    """Run Alfred's local-only stdio MCP server."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
