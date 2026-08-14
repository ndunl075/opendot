"""Conservative, approval-gated learning of repeated Alfred tool workflows.

Only structural metadata is observed: tool names, argument names, and a tiny
allowlist of non-content routing values. Prompts and argument values such as
names, titles, dates, message bodies, and addresses never enter these tables.
Successful turns must repeat across multiple days before a versioned skill
proposal is created. A proposal is inert Markdown until a separate human
approval is reviewed. Activation and execution are deliberately outside this
slice, so merely approving a proposal cannot change Hermes's skill files.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database
from .policy import PolicyError


WORKFLOW_TURN_ID_ENV = "ALFRED_WORKFLOW_TURN_ID"
WORKFLOW_REVIEW_ACTION = "workflow_skill_review"

_SAFE_LITERAL_ARGUMENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("connector_records_get", "connector"),
        ("connector_records_get", "record_type"),
        ("memory_feedback", "outcome"),
        ("remember", "kind"),
        ("remember", "sensitivity"),
    }
)
_SAFE_LITERAL = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]{2,79}$")
_EXCLUDED_TOOLS = frozenset({"action_commit", "memory_feedback", "system_status"})


class WorkflowStep(BaseModel):
    tool: str
    argument_keys: list[str] = Field(default_factory=list)
    literals: dict[str, str] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    schema_version: int = 1
    name: str
    description: str
    steps: list[WorkflowStep]
    approval_required: bool = True
    generated_from_content: bool = False


class WorkflowSkillVersion(BaseModel):
    id: str
    pattern_signature: str
    skill_name: str
    version: int
    state: str
    definition: WorkflowDefinition
    skill_markdown: str
    diff_text: str
    content_hash: str
    occurrence_count: int
    distinct_days: int
    first_observed_at: datetime
    last_observed_at: datetime
    approval_id: str | None = None
    activated_path: str | None = None
    created_at: datetime
    activated_at: datetime | None = None


class WorkflowScanResult(BaseModel):
    eligible_patterns: int
    proposed: list[WorkflowSkillVersion] = Field(default_factory=list)


class WorkflowObservationStore:
    """Record successful tool structure for one correlated agent turn."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record_tool_call(
        self,
        turn_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> int:
        normalized_turn = turn_id.strip()
        normalized_tool = tool_name.strip()
        if not normalized_turn or not normalized_tool:
            raise ValueError("workflow turn and tool names cannot be empty")
        now = (observed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        payload = self.sanitize_arguments(normalized_tool, arguments)
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO workflow_turns (turn_id, state, first_observed_at)
                    VALUES (?, 'pending', ?)
                    ON CONFLICT(turn_id) DO NOTHING
                    """,
                    (normalized_turn, now),
                )
                row = connection.execute(
                    "SELECT COALESCE(MAX(step_index), -1) + 1 FROM workflow_tool_observations WHERE turn_id = ?",
                    (normalized_turn,),
                ).fetchone()
                step_index = int(row[0])
                connection.execute(
                    """
                    INSERT INTO workflow_tool_observations (
                        id, turn_id, step_index, tool_name, arguments_json, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        normalized_turn,
                        step_index,
                        normalized_tool,
                        _json(payload),
                        now,
                    ),
                )
        return step_index

    def complete_turn(
        self,
        turn_id: str,
        *,
        outcome: str,
        completed_at: datetime | None = None,
    ) -> None:
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                self.complete_turn_in_transaction(
                    connection,
                    turn_id,
                    outcome=outcome,
                    completed_at=completed_at,
                )

    @staticmethod
    def complete_turn_in_transaction(
        connection,
        turn_id: str,
        *,
        outcome: str,
        completed_at: datetime | None = None,
    ) -> None:
        if outcome not in {"ok", "error"}:
            raise ValueError("workflow turn outcome must be ok or error")
        now = (completed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        connection.execute(
            """
            UPDATE workflow_turns
            SET state = ?, completed_at = ?
            WHERE turn_id = ?
            """,
            (outcome, now, turn_id),
        )

    @staticmethod
    def sanitize_arguments(tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        keys = sorted(str(key) for key in arguments if str(key).strip())
        literals: dict[str, str] = {}
        for key in keys:
            value = arguments.get(key)
            if (
                (tool_name, key) in _SAFE_LITERAL_ARGUMENTS
                and isinstance(value, str)
                and _SAFE_LITERAL.fullmatch(value)
            ):
                literals[key] = value
        return {"argument_keys": keys, "literals": literals}


class WorkflowLearningService:
    """Find repeated successful tool sequences and propose inert skill versions."""

    def __init__(
        self,
        database: Database,
        *,
        minimum_occurrences: int = 3,
        minimum_distinct_days: int = 2,
        lookback_days: int = 90,
        max_steps: int = 8,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if minimum_occurrences < 2:
            raise ValueError("workflow learning requires at least two occurrences")
        if minimum_distinct_days < 1:
            raise ValueError("minimum distinct days must be positive")
        self.database = database
        self.minimum_occurrences = minimum_occurrences
        self.minimum_distinct_days = minimum_distinct_days
        self.lookback_days = lookback_days
        self.max_steps = max_steps
        self._now = now or (lambda: datetime.now(UTC))

    def scan(self, *, actor: str = "owner:workflow-learning") -> WorkflowScanResult:
        grouped = self._eligible_patterns()
        proposed: list[WorkflowSkillVersion] = []
        for signature, occurrences in sorted(grouped.items()):
            definition = self._definition_for(signature, occurrences)
            latest = self._latest_for_name(definition.name)
            if not self._should_propose(signature, occurrences, latest):
                continue
            version = 1 if latest is None else latest.version + 1
            markdown = self._render_skill(definition, version, occurrences)
            previous = latest.skill_markdown if latest is not None else ""
            diff_text = self._diff(definition.name, version, previous, markdown)
            content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            created = self._now().astimezone(UTC)
            version_id = str(uuid4())
            approval_id = str(uuid4())
            preview = {
                "skill_name": definition.name,
                "version": version,
                "occurrences": len(occurrences),
                "distinct_days": len({item["day"] for item in occurrences}),
                "diff": diff_text,
                "content_hash": content_hash,
            }
            with self.database.connect() as connection:
                with self.database.transaction(connection):
                    connection.execute(
                        """
                        INSERT INTO approvals (
                            id, requested_at, expires_at, actor, action_type,
                            preview_json, state
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                        """,
                        (
                            approval_id,
                            created.isoformat(),
                            (created + timedelta(days=7)).isoformat(),
                            actor,
                            WORKFLOW_REVIEW_ACTION,
                            _json(preview),
                        ),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor=actor,
                            client="policy",
                            tool="approval_propose",
                            outcome="ok",
                            result={
                                "approval_id": approval_id,
                                "action_type": WORKFLOW_REVIEW_ACTION,
                            },
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO workflow_skill_versions (
                            id, pattern_signature, skill_name, version, state,
                            definition_json, skill_markdown, diff_text, content_hash,
                            occurrence_count, distinct_days, first_observed_at,
                            last_observed_at, approval_id, created_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id,
                            signature,
                            definition.name,
                            version,
                            _json(definition.model_dump(mode="json")),
                            markdown,
                            diff_text,
                            content_hash,
                            len(occurrences),
                            len({item["day"] for item in occurrences}),
                            min(item["observed_at"] for item in occurrences).isoformat(),
                            max(item["observed_at"] for item in occurrences).isoformat(),
                            approval_id,
                            created.isoformat(),
                        ),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:workflow_learning",
                            client="workflow_learning",
                            tool="workflow_skill_propose",
                            outcome="ok",
                            result={
                                "version_id": version_id,
                                "skill_name": definition.name,
                                "version": str(version),
                                "occurrences": str(len(occurrences)),
                                "approval_id": approval_id,
                            },
                        ),
                    )
            proposed.append(self.get(version_id))
        return WorkflowScanResult(eligible_patterns=len(grouped), proposed=proposed)

    def list_versions(self, *, state: str | None = None) -> list[WorkflowSkillVersion]:
        self.database.migrate()
        with self.database.connect() as connection:
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM workflow_skill_versions ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM workflow_skill_versions WHERE state = ? ORDER BY created_at DESC",
                    (state,),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, version_id: str) -> WorkflowSkillVersion:
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_skill_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"workflow skill version does not exist: {version_id}")
        return self._from_row(row)

    def reject(self, version_id: str, *, actor: str) -> WorkflowSkillVersion:
        version = self.get(version_id)
        if version.state != "pending" or not version.approval_id:
            raise PolicyError(f"workflow skill is not pending: {version.state}")
        decided_at = self._now().astimezone(UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                approval = connection.execute(
                    "SELECT * FROM approvals WHERE id = ?",
                    (version.approval_id,),
                ).fetchone()
                if approval is None:
                    raise PolicyError("workflow review approval does not exist")
                if approval["actor"] != actor:
                    raise PolicyError("only the requesting actor can reject this action")
                if approval["action_type"] != WORKFLOW_REVIEW_ACTION:
                    raise PolicyError("approval is not for workflow review")
                if approval["state"] != "pending":
                    raise PolicyError(f"approval is not pending: {approval['state']}")
                if datetime.fromisoformat(approval["expires_at"]) <= decided_at:
                    raise PolicyError("approval has expired")
                connection.execute(
                    "UPDATE approvals SET state = 'rejected' WHERE id = ? AND state = 'pending'",
                    (version.approval_id,),
                )
                changed = connection.execute(
                    """
                    UPDATE workflow_skill_versions SET state = 'rejected'
                    WHERE id = ? AND state = 'pending'
                    """,
                    (version_id,),
                ).rowcount
                if changed != 1:
                    raise PolicyError("workflow review raced with another decision")
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor=actor,
                        client="workflow_learning",
                        tool="workflow_skill_reject",
                        outcome="ok",
                        result={"version_id": version_id},
                    ),
                )
        return self.get(version_id)

    def accept(self, version_id: str, *, actor: str) -> WorkflowSkillVersion:
        """Accept the reviewed diff without installing or executing the skill."""

        version = self.get(version_id)
        if version.state != "pending" or not version.approval_id:
            raise PolicyError(f"workflow skill is not pending: {version.state}")
        decided_at = self._now().astimezone(UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                approval = connection.execute(
                    "SELECT * FROM approvals WHERE id = ?",
                    (version.approval_id,),
                ).fetchone()
                if approval is None:
                    raise PolicyError("workflow review approval does not exist")
                if approval["actor"] != actor:
                    raise PolicyError("only the requesting actor can accept this workflow")
                if approval["action_type"] != WORKFLOW_REVIEW_ACTION:
                    raise PolicyError("approval is not for workflow review")
                if approval["state"] != "pending":
                    raise PolicyError(f"approval is not pending: {approval['state']}")
                if datetime.fromisoformat(approval["expires_at"]) <= decided_at:
                    raise PolicyError("approval has expired")
                connection.execute(
                    """
                    UPDATE approvals
                    SET state = 'approved', approved_at = ?, approved_by = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (decided_at.isoformat(), actor, version.approval_id),
                )
                changed = connection.execute(
                    """
                    UPDATE workflow_skill_versions SET state = 'accepted'
                    WHERE id = ? AND state = 'pending'
                    """,
                    (version_id,),
                ).rowcount
                if changed != 1:
                    raise PolicyError("workflow review raced with another decision")
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor=actor,
                        client="workflow_learning",
                        tool="workflow_skill_accept",
                        outcome="ok",
                        result={"version_id": version_id},
                    ),
                )
        return self.get(version_id)

    def _eligible_patterns(self) -> dict[str, list[dict[str, Any]]]:
        self.database.migrate()
        cutoff = (self._now().astimezone(UTC) - timedelta(days=self.lookback_days)).isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT o.turn_id, o.step_index, o.tool_name, o.arguments_json,
                       o.observed_at
                FROM workflow_tool_observations o
                JOIN workflow_turns t ON t.turn_id = o.turn_id
                WHERE t.state = 'ok' AND o.observed_at >= ?
                ORDER BY o.turn_id, o.step_index
                """,
                (cutoff,),
            ).fetchall()
        turns: dict[str, list[Any]] = defaultdict(list)
        unsafe_turns = {
            str(row["turn_id"])
            for row in rows
            if row["tool_name"] in _EXCLUDED_TOOLS
        }
        for row in rows:
            if row["turn_id"] not in unsafe_turns:
                turns[row["turn_id"]].append(row)
        patterns: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for turn_id, steps in turns.items():
            if not 2 <= len(steps) <= self.max_steps:
                continue
            rendered_steps = [
                {
                    "tool": row["tool_name"],
                    **json.loads(row["arguments_json"]),
                }
                for row in steps
            ]
            signature = hashlib.sha256(_json(rendered_steps).encode("utf-8")).hexdigest()
            observed_at = datetime.fromisoformat(steps[0]["observed_at"])
            patterns[signature].append(
                {
                    "turn_id": turn_id,
                    "steps": rendered_steps,
                    "observed_at": observed_at,
                    "day": observed_at.date().isoformat(),
                }
            )
        return {
            signature: occurrences
            for signature, occurrences in patterns.items()
            if len(occurrences) >= self.minimum_occurrences
            and len({item["day"] for item in occurrences}) >= self.minimum_distinct_days
        }

    def _definition_for(
        self, signature: str, occurrences: list[dict[str, Any]]
    ) -> WorkflowDefinition:
        steps = [WorkflowStep.model_validate(item) for item in occurrences[0]["steps"]]
        name = self._skill_name(steps, signature)
        labels = " -> ".join(step.tool for step in steps)
        return WorkflowDefinition(
            name=name,
            description=(
                f"Approval-gated workflow suggested from repeated successful Alfred turns: {labels}."
            ),
            steps=steps,
        )

    @staticmethod
    def _skill_name(steps: list[WorkflowStep], signature: str) -> str:
        first = steps[0].tool.replace("_", "-")
        last = steps[-1].tool.replace("_", "-")
        base = f"learned-{first}-to-{last}"
        if len(base) > 72:
            base = f"{base[:65].rstrip('-')}-{signature[:6]}"
        if not _SKILL_NAME.fullmatch(base):
            raise ValueError("generated workflow skill name is invalid")
        return base

    def _should_propose(
        self,
        signature: str,
        occurrences: list[dict[str, Any]],
        latest: WorkflowSkillVersion | None,
    ) -> bool:
        if latest is None:
            return True
        if latest.state in {"draft", "pending"}:
            return False
        if latest.pattern_signature == signature:
            return latest.state == "rejected" and len(occurrences) >= latest.occurrence_count + 2
        return True

    @staticmethod
    def _render_skill(
        definition: WorkflowDefinition,
        version: int,
        occurrences: list[dict[str, Any]],
    ) -> str:
        lines = [
            "---",
            f"name: {definition.name}",
            f"description: {definition.description}",
            f"version: 0.1.{version}",
            "author: Alfred workflow learning",
            "license: Apache-2.0",
            "metadata:",
            "  alfred:",
            "    generated: true",
            f"    evidence_count: {len(occurrences)}",
            f"    distinct_days: {len({item['day'] for item in occurrences})}",
            "    activation: human-approval-required",
            "---",
            "",
            f"# {definition.name}",
            "",
            definition.description,
            "",
            "## Safety boundary",
            "",
            "- Use this workflow only when the current request clearly matches it.",
            "- Never infer missing people, destinations, content, dates, or identifiers from past runs.",
            "- Every underlying Alfred tool keeps its own scopes, previews, and approval requirements.",
            "- This skill cannot call `action_commit` or approve its own consequential actions.",
            "",
            "## Steps",
            "",
        ]
        for index, step in enumerate(definition.steps, start=1):
            lines.append(f"{index}. Call `{step.tool}`.")
            if step.literals:
                values = ", ".join(f"`{key}={value}`" for key, value in sorted(step.literals.items()))
                lines.append(f"   - Fixed routing values observed repeatedly: {values}.")
            variable_keys = [key for key in step.argument_keys if key not in step.literals]
            if variable_keys:
                values = ", ".join(f"`{key}`" for key in variable_keys)
                lines.append(f"   - Resolve from the current request only: {values}.")
        lines.extend(
            [
                "",
                "## Verification",
                "",
                "- Confirm every variable input came from the current request or an explicit tool result.",
                "- Stop at any preview or approval boundary and wait for the owner.",
                "- Report partial completion honestly if a tool fails.",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _diff(name: str, version: int, previous: str, current: str) -> str:
        before = previous.splitlines(keepends=True)
        after = current.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"{name}/SKILL.md@{version - 1}" if previous else "/dev/null",
                tofile=f"{name}/SKILL.md@{version}",
            )
        )

    def _latest_for_name(self, name: str) -> WorkflowSkillVersion | None:
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_skill_versions
                WHERE skill_name = ? ORDER BY version DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row) -> WorkflowSkillVersion:
        return WorkflowSkillVersion(
            id=row["id"],
            pattern_signature=row["pattern_signature"],
            skill_name=row["skill_name"],
            version=int(row["version"]),
            state=row["state"],
            definition=WorkflowDefinition.model_validate(json.loads(row["definition_json"])),
            skill_markdown=row["skill_markdown"],
            diff_text=row["diff_text"],
            content_hash=row["content_hash"],
            occurrence_count=int(row["occurrence_count"]),
            distinct_days=int(row["distinct_days"]),
            first_observed_at=datetime.fromisoformat(row["first_observed_at"]),
            last_observed_at=datetime.fromisoformat(row["last_observed_at"]),
            approval_id=row["approval_id"],
            activated_path=row["activated_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            activated_at=datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None,
        )


def current_workflow_turn_id() -> str | None:
    value = os.environ.get(WORKFLOW_TURN_ID_ENV, "").strip()
    return value or None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
