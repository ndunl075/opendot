from datetime import UTC, datetime, timedelta

import httpx
import pytest

from alfred.github import GitHubClient
from alfred.pull_requests import PullRequestService


def _search_item(
    *,
    title: str,
    url: str,
    updated_at: str,
    repo: str = "example/alfred",
    author: str = "nico",
    draft: bool = False,
) -> dict:
    return {
        "title": title,
        "html_url": url,
        "updated_at": updated_at,
        "repository_url": f"https://api.github.com/repos/{repo}",
        "user": {"login": author},
        "draft": draft,
    }


class FakePullRequestSearch:
    def __init__(self, *, authored: list[dict] | None = None, review_requested: list[dict] | None = None) -> None:
        self.authored = authored or []
        self.review_requested = review_requested or []
        self.queries: list[str] = []

    def search_issues(self, query: str) -> list[dict]:
        self.queries.append(query)
        if query == "is:pr is:open author:@me":
            return list(self.authored)
        if query == "is:pr is:open review-requested:@me":
            return list(self.review_requested)
        raise AssertionError(f"unexpected query: {query}")


def test_pull_request_service_merges_authored_and_review_requested() -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    transport = FakePullRequestSearch(
        authored=[_search_item(title="My feature", url="https://github.com/example/alfred/pull/1", updated_at="2026-08-15T10:00:00Z")],
        review_requested=[
            _search_item(
                title="Needs review",
                url="https://github.com/example/other/pull/9",
                updated_at="2026-08-14T10:00:00Z",
                repo="example/other",
                author="friend",
            )
        ],
    )

    report = PullRequestService(transport, stale_after_days=14).get(now=now)

    assert transport.queries == [
        "is:pr is:open author:@me",
        "is:pr is:open review-requested:@me",
    ]
    assert [item.title for item in report.pull_requests] == ["My feature", "Needs review"]
    assert report.pull_requests[0].role == "authored"
    assert report.pull_requests[1].role == "review_requested"
    assert all(item.stale is False for item in report.pull_requests)


def test_pull_request_service_marks_stale_when_updated_before_threshold() -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    transport = FakePullRequestSearch(
        authored=[
            _search_item(
                title="Fresh",
                url="https://github.com/example/alfred/pull/1",
                updated_at="2026-08-10T10:00:00Z",
            ),
            _search_item(
                title="Old",
                url="https://github.com/example/alfred/pull/2",
                updated_at="2026-07-01T10:00:00Z",
            ),
        ]
    )

    report = PullRequestService(transport, stale_after_days=14).get(now=now)

    by_title = {item.title: item for item in report.pull_requests}
    assert by_title["Fresh"].stale is False
    assert by_title["Old"].stale is True
    assert "stale >14d" in report.render()


def test_pull_request_service_prefers_review_requested_on_duplicate_url() -> None:
    url = "https://github.com/example/alfred/pull/1"
    transport = FakePullRequestSearch(
        authored=[_search_item(title="Mine", url=url, updated_at="2026-08-15T10:00:00Z")],
        review_requested=[_search_item(title="Mine", url=url, updated_at="2026-08-15T10:00:00Z", author="friend")],
    )

    report = PullRequestService(transport).get(now=datetime(2026, 8, 16, tzinfo=UTC))

    assert len(report.pull_requests) == 1
    assert report.pull_requests[0].role == "review_requested"


def test_pull_request_service_render_includes_draft_flag() -> None:
    transport = FakePullRequestSearch(
        authored=[
            _search_item(
                title="WIP",
                url="https://github.com/example/alfred/pull/3",
                updated_at="2026-08-15T10:00:00Z",
                draft=True,
            )
        ]
    )

    rendered = PullRequestService(transport).get(now=datetime(2026, 8, 16, tzinfo=UTC)).render()

    assert "[draft]" in rendered
    assert "WIP" in rendered


def test_pull_request_service_rejects_non_positive_stale_threshold() -> None:
    with pytest.raises(ValueError, match="stale_after_days"):
        PullRequestService(FakePullRequestSearch(), stale_after_days=0)


def test_github_client_search_issues_uses_the_search_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/search/issues"
        seen.append(request.url.params["q"])
        assert request.url.params["sort"] == "updated"
        assert request.url.params["order"] == "desc"
        return httpx.Response(
            200,
            json={
                "items": [
                    _search_item(
                        title="Fix bug",
                        url="https://github.com/example/alfred/pull/4",
                        updated_at="2026-08-15T10:00:00Z",
                    )
                ]
            },
        )

    client = GitHubClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        items = client.search_issues("is:pr is:open author:@me")
    finally:
        client.close()

    assert seen == ["is:pr is:open author:@me"]
    assert items[0]["title"] == "Fix bug"


def test_github_client_search_issues_paginates_until_short_page() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        page = int(request.url.params["page"])
        per_page = int(request.url.params["per_page"])
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "items": [
                        _search_item(
                            title=f"PR {index}",
                            url=f"https://github.com/example/alfred/pull/{index}",
                            updated_at="2026-08-15T10:00:00Z",
                        )
                        for index in range(per_page)
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    _search_item(
                        title="Last page",
                        url="https://github.com/example/alfred/pull/999",
                        updated_at="2026-08-15T10:00:00Z",
                    )
                ]
            },
        )

    client = GitHubClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        items = client.search_issues("is:pr is:open author:@me", per_page=2)
    finally:
        client.close()

    assert calls == 2
    assert len(items) == 3
    assert items[-1]["title"] == "Last page"


def test_pull_request_service_sorts_non_stale_before_stale() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    transport = FakePullRequestSearch(
        authored=[
            _search_item(
                title="Stale but recent title",
                url="https://github.com/example/alfred/pull/1",
                updated_at=(now - timedelta(days=20)).isoformat().replace("+00:00", "Z"),
            ),
            _search_item(
                title="Active",
                url="https://github.com/example/alfred/pull/2",
                updated_at=(now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            ),
        ]
    )

    report = PullRequestService(transport, stale_after_days=14).get(now=now)

    assert [item.title for item in report.pull_requests] == ["Active", "Stale but recent title"]
