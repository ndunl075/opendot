"""Live snapshot of open GitHub pull requests authored by or awaiting review from the owner.

Deliberately separate from notifications sync: the inbox feed lacks reliable
open/closed state and is mostly CI noise. Two targeted search queries give a
clean PR-only view on demand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel

PullRequestRole = Literal["authored", "review_requested"]

_AUTHORED_QUERY = "is:pr is:open author:@me"
_REVIEW_REQUESTED_QUERY = "is:pr is:open review-requested:@me"


class PullRequestSummary(BaseModel):
    title: str
    repo: str
    url: str
    author: str | None
    draft: bool
    updated_at: datetime
    role: PullRequestRole
    stale: bool


class PullRequestReport(BaseModel):
    generated_at: datetime
    stale_after_days: int
    pull_requests: list[PullRequestSummary]

    def render(self) -> str:
        authored = sum(1 for item in self.pull_requests if item.role == "authored")
        review = sum(1 for item in self.pull_requests if item.role == "review_requested")
        stale = sum(1 for item in self.pull_requests if item.stale)
        lines = [
            f"Open pull requests — {self.generated_at.date().isoformat()}",
            (
                f"Found {len(self.pull_requests)} open PR(s)"
                f" ({authored} authored, {review} awaiting review"
                + (f"; {stale} stale" if stale else "")
                + ")."
            ),
        ]
        if not self.pull_requests:
            lines.append("\nNothing open right now.")
            return "\n".join(lines)
        lines.append("")
        for item in self.pull_requests:
            flags: list[str] = []
            if item.draft:
                flags.append("draft")
            if item.stale:
                flags.append(f"stale >{self.stale_after_days}d")
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            who = f" — {item.author}" if item.author else ""
            role = "authored" if item.role == "authored" else "review requested"
            lines.append(
                f"- {item.title} ({item.repo}){who} [{role}]{flag_text} <{item.url}>"
            )
        return "\n".join(lines)


class PullRequestTransport(Protocol):
    def search_issues(self, query: str) -> list[dict[str, Any]]: ...


class PullRequestService:
    def __init__(self, transport: PullRequestTransport, *, stale_after_days: int = 14) -> None:
        if stale_after_days <= 0:
            raise ValueError("stale_after_days must be positive")
        self.transport = transport
        self.stale_after_days = stale_after_days

    def get(self, *, now: datetime | None = None) -> PullRequestReport:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        stale_cutoff = generated_at - timedelta(days=self.stale_after_days)
        merged: dict[str, PullRequestSummary] = {}
        for role, query in (
            ("authored", _AUTHORED_QUERY),
            ("review_requested", _REVIEW_REQUESTED_QUERY),
        ):
            for item in self.transport.search_issues(query):
                summary = _normalize_pull_request(item, role=role, stale_cutoff=stale_cutoff)
                existing = merged.get(summary.url)
                if existing is None or _role_precedence(summary.role) > _role_precedence(existing.role):
                    merged[summary.url] = summary
        pull_requests = sorted(
            merged.values(),
            key=lambda item: (not item.stale, item.updated_at, item.title.lower()),
            reverse=True,
        )
        return PullRequestReport(
            generated_at=generated_at,
            stale_after_days=self.stale_after_days,
            pull_requests=pull_requests,
        )


def _role_precedence(role: PullRequestRole) -> int:
    return 1 if role == "review_requested" else 0


def _normalize_pull_request(
    item: dict[str, Any],
    *,
    role: PullRequestRole,
    stale_cutoff: datetime,
) -> PullRequestSummary:
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Untitled pull request"
    url = item.get("html_url")
    if not isinstance(url, str) or not url:
        raise ValueError("GitHub pull request is missing html_url")
    updated_raw = item.get("updated_at")
    if not isinstance(updated_raw, str):
        raise ValueError("GitHub pull request is missing updated_at")
    updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00")).astimezone(UTC)
    author_raw = item.get("user")
    author = author_raw.get("login") if isinstance(author_raw, dict) else None
    if author is not None and not isinstance(author, str):
        author = None
    draft = _is_draft(item)
    repo = _repository_name(item)
    return PullRequestSummary(
        title=title,
        repo=repo,
        url=url,
        author=author,
        draft=draft,
        updated_at=updated_at,
        role=role,
        stale=updated_at < stale_cutoff,
    )


def _repository_name(item: dict[str, Any]) -> str:
    repository_url = item.get("repository_url")
    if isinstance(repository_url, str) and repository_url.startswith("https://api.github.com/repos/"):
        return repository_url.removeprefix("https://api.github.com/repos/")
    repository = item.get("repository")
    if isinstance(repository, dict):
        full_name = repository.get("full_name")
        if isinstance(full_name, str) and full_name:
            return full_name
    raise ValueError("GitHub pull request is missing repository")


def _is_draft(item: dict[str, Any]) -> bool:
    draft = item.get("draft")
    if isinstance(draft, bool):
        return draft
    pull_request = item.get("pull_request")
    if isinstance(pull_request, dict) and isinstance(pull_request.get("draft"), bool):
        return pull_request["draft"]
    return False
