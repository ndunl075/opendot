"""Execute an already-approved action through its connector-specific safe path."""

from __future__ import annotations

from typing import Any

from .db import Database
from .gmail import GmailActions, GmailClient, GmailSendActions
from .github import GitHubActions, GitHubClient
from .google_calendar import GoogleCalendarActions, GoogleCalendarClient
from .google_oauth import current_access_token
from .memory_graph import MemoryActions
from .policy import ApprovalService, PolicyError
from .secret_store import SecretStore, SystemKeyringSecretStore


class ActionExecutor:
    """Keep MCP and Telegram approvals on one idempotent execution boundary."""

    def __init__(self, database: Database, secret_store: SecretStore | None = None) -> None:
        self.database = database
        self.approvals = ApprovalService(database)
        self.secrets = secret_store or SystemKeyringSecretStore()

    def execute(self, approval_id: str, *, actor: str, token: str) -> dict[str, Any]:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise PolicyError("approval does not exist")
        if approval.action_type == "memory_forget":
            return MemoryActions(self.database, self.approvals).execute_forget(
                approval_id, actor=actor, token=token
            ).model_dump(mode="json")
        if approval.action_type == "calendar_event_create":
            client = GoogleCalendarClient(current_access_token(self.secrets))
            try:
                return GoogleCalendarActions(self.database, self.approvals, client).execute(
                    approval_id, actor=actor, token=token
                ).model_dump(mode="json")
            finally:
                client.close()
        if approval.action_type == "gmail_draft_create":
            client = GmailClient(current_access_token(self.secrets))
            try:
                return GmailActions(self.database, self.approvals, client).execute(
                    approval_id, actor=actor, token=token
                ).model_dump(mode="json")
            finally:
                client.close()
        if approval.action_type == "gmail_message_send":
            client = GmailClient(current_access_token(self.secrets))
            try:
                return GmailSendActions(self.database, self.approvals, client).execute(
                    approval_id, actor=actor, token=token
                ).model_dump(mode="json")
            finally:
                client.close()
        if approval.action_type == "github_issue_create":
            client = GitHubClient(self.secrets.get_required("github-issue-token"))
            try:
                return GitHubActions(self.database, self.approvals, client).execute(
                    approval_id, actor=actor, token=token
                ).model_dump(mode="json")
            finally:
                client.close()
        raise PolicyError(f"action execution does not support: {approval.action_type}")
