from pathlib import Path

import httpx
import pytest

from alfred.composio import (
    ACTION_TYPE,
    DEFAULT_MONTHLY_CALL_LIMIT,
    SECRET_NAME,
    ComposioActions,
    ComposioClient,
    ComposioError,
    ComposioQuotaExceeded,
    ComposioReservedToolkit,
    assert_overflow_toolkit,
    calls_this_month,
    tool_writes,
)
from alfred.db import Database
from alfred.policy import ApprovalService, PolicyError


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "GET" and path.endswith("/tools"):
        query = request.url.params.get("query", "")
        toolkit = request.url.params.get("toolkit_slug")
        items = [
            {
                "slug": "NOTION_FETCH_DATA",
                "name": "Fetch Notion data",
                "description": "Read pages",
                "toolkit": {"slug": "notion"},
                "tags": ["read"],
                "no_auth": False,
                "input_parameters": {"required": ["page_id"]},
            },
            {
                "slug": "NOTION_CREATE_PAGE",
                "name": "Create Notion page",
                "description": "Write a page",
                "toolkit": {"slug": "notion"},
                "tags": ["write"],
                "no_auth": False,
                "input_parameters": {"required": ["parent_id", "title"]},
            },
            {
                "slug": "GMAIL_FETCH_EMAILS",
                "name": "Fetch emails",
                "description": "should be filtered",
                "toolkit": {"slug": "gmail"},
                "tags": ["read"],
                "no_auth": False,
                "input_parameters": {},
            },
        ]
        if toolkit:
            items = [item for item in items if item["toolkit"]["slug"] == toolkit]
        if query.lower() == "create":
            items = [item for item in items if "CREATE" in item["slug"]]
        return httpx.Response(200, json={"items": items, "total_items": len(items)})
    if request.method == "GET" and "/tools/" in path:
        slug = path.rsplit("/", 1)[-1]
        writes = "CREATE" in slug or "SEND" in slug
        toolkit = slug.split("_", 1)[0].lower()
        return httpx.Response(
            200,
            json={
                "slug": slug,
                "name": slug,
                "description": "tool",
                "toolkit": {"slug": toolkit},
                "tags": ["write" if writes else "read"],
                "input_parameters": {"required": ["q"] if not writes else ["title"]},
            },
        )
    if request.method == "POST" and "/tools/execute/" in path:
        return httpx.Response(200, json={"successful": True, "data": {"ok": True, "path": path}, "error": None})
    if request.method == "GET" and path.endswith("/connected_accounts"):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ca_notion",
                        "status": "ACTIVE",
                        "alias": "personal",
                        "toolkit": {"slug": "notion"},
                    }
                ]
            },
        )
    if request.method == "GET" and path.endswith("/auth_configs"):
        return httpx.Response(200, json={"items": []})
    if request.method == "POST" and path.endswith("/auth_configs"):
        return httpx.Response(
            201,
            json={"auth_config": {"id": "ac_notion", "auth_scheme": "OAUTH2", "is_composio_managed": True}},
        )
    if request.method == "POST" and path.endswith("/connected_accounts/link"):
        return httpx.Response(
            201,
            json={
                "redirect_url": "https://connect.composio.dev/link/ln_test",
                "expires_at": "2026-08-18T12:00:00Z",
                "connected_account_id": "ca_new",
            },
        )
    return httpx.Response(404, json={"error": {"message": f"unexpected {request.method} {path}"}})


def _client(database: Database | None = None, *, monthly_limit: int | None = None) -> ComposioClient:
    return ComposioClient(
        "test-key",
        database=database,
        transport=httpx.MockTransport(_handler),
        monthly_limit=monthly_limit,
    )


def test_write_detection_defaults_unknown_slugs_to_write() -> None:
    assert tool_writes("NOTION_FETCH_DATA", ("read",)) is False
    assert tool_writes("NOTION_CREATE_PAGE") is True
    assert tool_writes("SPOTIFY_PLAY_TRACK") is True
    assert tool_writes("WEIRD_TOOL") is True


def test_reserved_toolkits_are_rejected() -> None:
    with pytest.raises(ComposioReservedToolkit, match="first-party"):
        assert_overflow_toolkit("gmail")
    with pytest.raises(ComposioReservedToolkit, match="first-party"):
        assert_overflow_toolkit("Google Calendar")
    assert assert_overflow_toolkit("Notion") == "notion"


def test_search_hides_first_party_gmail_tools() -> None:
    client = _client()
    try:
        tools = client.search_tools("pages")
    finally:
        client.close()

    assert [tool.slug for tool in tools] == ["NOTION_FETCH_DATA", "NOTION_CREATE_PAGE"]
    assert tools[0].writes is False
    assert tools[1].writes is True
    assert tools[0].input_fields == ("page_id",)


def test_connect_creates_managed_auth_config_when_none_exists(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    client = _client(database)
    try:
        link = client.connect("notion")
    finally:
        client.close()

    assert link.redirect_url.startswith("https://connect.composio.dev/")
    assert calls_this_month(database) >= 2


def test_read_executes_immediately_and_write_proposes(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    client = _client(database)
    actions = ComposioActions(database, approvals, client)
    try:
        read = actions.execute_or_propose(actor="nico", slug="NOTION_FETCH_DATA", arguments={"q": "inbox"})
        write = actions.execute_or_propose(actor="nico", slug="NOTION_CREATE_PAGE", arguments={"title": "Notes"})
    finally:
        client.close()

    assert read["needs_approval"] is False
    assert read["result"]["successful"] is True
    assert write["needs_approval"] is True
    assert write["approval"]["action_type"] == ACTION_TYPE
    assert write["approval"]["preview"]["slug"] == "NOTION_CREATE_PAGE"


def test_approved_write_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    client = _client(database)
    actions = ComposioActions(database, approvals, client)
    try:
        proposal = actions.propose(actor="nico", slug="NOTION_CREATE_PAGE", arguments={"title": "Notes"})
        issued = approvals.approve(proposal.id, actor="nico")
        first = actions.execute(proposal.id, actor="nico", token=issued.token)
        second = actions.execute(proposal.id, actor="nico", token=issued.token)
    finally:
        client.close()

    assert first.replayed is False
    assert second.replayed is True
    assert first.successful is True
    assert second.data == first.data


def test_execute_rejects_wrong_action_type(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    approval = approvals.propose(actor="nico", action_type="github_issue_create", preview={"title": "x"})
    issued = approvals.approve(approval.id, actor="nico")
    with pytest.raises(PolicyError, match="Composio"):
        ComposioActions(database, approvals, _client()).execute(approval.id, actor="nico", token=issued.token)


def test_quota_blocks_before_a_call_that_would_exceed_the_cap(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    client = _client(database, monthly_limit=1)
    try:
        client.search_tools("pages")
        with pytest.raises(ComposioQuotaExceeded, match="cap reached"):
            client.search_tools("pages")
    finally:
        client.close()


def test_status_lists_accounts_and_remaining_quota(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    client = _client(database)
    try:
        status = ComposioActions(database, ApprovalService(database), client).status()
    finally:
        client.close()

    assert status.configured is True
    assert status.monthly_limit == DEFAULT_MONTHLY_CALL_LIMIT
    assert status.accounts[0].toolkit == "notion"
    assert status.remaining == status.monthly_limit - status.calls_this_month


def test_http_error_surfaces_composio_message() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key", "suggested_fix": "rotate it"}})

    client = ComposioClient("bad", transport=httpx.MockTransport(failing), monthly_limit=10)
    try:
        with pytest.raises(ComposioError, match="invalid api key"):
            client.search_tools("x")
    finally:
        client.close()


def test_secret_name_is_the_keyring_account() -> None:
    assert SECRET_NAME == "composio-api-key"
