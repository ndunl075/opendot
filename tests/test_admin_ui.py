from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from alfred.admin_ui import (
    _CONNECTOR_ICON_PATHS,
    _connector_icon,
    _format_dt,
    _rate,
    _result_preview,
    _status_label,
    create_admin_app,
    run_admin_ui,
)
from alfred.audit import AuditEvent, AuditLog
from alfred.db import Database
from alfred.events import EventStore
from alfred.policy import ApprovalService
from alfred.tasks import TaskStore

TOKEN = "test-admin-token"


def _client(database: Database) -> TestClient:
    return TestClient(create_admin_app(database, bearer_token_value=TOKEN))


def _login(client: TestClient) -> None:
    response = client.post("/login", data={"token": TOKEN, "next": "/"})
    assert response.status_code == 200  # TestClient follows the redirect by default


def test_unauthenticated_request_redirects_to_login(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_login_page_loads_without_authentication(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.get("/login")

    assert response.status_code == 200
    assert "Alfred admin" in response.text


def test_wrong_token_shows_an_error_and_sets_no_cookie(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.post("/login", data={"token": "not-the-token", "next": "/"})

    assert response.status_code == 200
    assert "Incorrect token" in response.text
    assert "alfred_admin_token" not in client.cookies


def test_correct_token_sets_a_cookie_and_grants_access(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    _login(client)

    assert client.cookies.get("alfred_admin_token") == TOKEN
    assert client.get("/").status_code == 200


def test_authorization_header_works_without_a_cookie(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.get("/", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


def test_wrong_bearer_header_still_redirects_to_login(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.get("/", headers={"Authorization": "Bearer wrong"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_static_stylesheet_is_reachable_without_authentication(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_overview_shows_an_overdue_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection, source="cli", external_id="t1", occurred_at=datetime.now(UTC), content="x", metadata={}
            )
            TaskStore.create(
                connection, title="Submit capstone draft", source_event_id=event.id, due_at=datetime.now(UTC) - timedelta(days=1)
            )
    client = _client(database)
    _login(client)

    response = client.get("/")

    assert "Submit capstone draft" in response.text
    assert "Overdue" in response.text


def test_overview_shows_the_empty_state_with_no_tasks(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))
    _login(client)

    response = client.get("/")

    assert "No open tasks" in response.text


def test_approvals_page_lists_a_pending_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    ApprovalService(database).propose(actor="nico", action_type="calendar_event_create", preview={"summary": "x"})
    client = _client(database)
    _login(client)

    response = client.get("/approvals")

    assert "calendar_event_create" in response.text
    assert "nico" in response.text


def test_approvals_page_shows_empty_state_with_nothing_pending(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))
    _login(client)

    response = client.get("/approvals")

    assert "Nothing waiting" in response.text


def test_connectors_page_shows_health_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                "VALUES ('gmail', 'self', NULL, ?, NULL, ?)",
                (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
    client = _client(database)
    _login(client)

    response = client.get("/connectors")

    assert "gmail" in response.text
    assert ">Connected<" in response.text
    assert '<svg class="connector-icon"' in response.text  # gmail has a real icon, not the generic fallback


def test_status_label_maps_health_states_to_human_readable_text() -> None:
    assert _status_label("ok") == "Connected"
    assert _status_label("error") == "Disconnected"
    assert _status_label("stale") == "Stale"
    assert _status_label("never_synced") == "Never connected"


def test_status_label_falls_back_to_title_case_for_an_unknown_state() -> None:
    assert _status_label("some_new_state") == "Some New State"


def test_connector_icon_returns_a_real_svg_for_every_known_connector() -> None:
    for connector in _CONNECTOR_ICON_PATHS:
        icon = _connector_icon(connector)
        assert icon.startswith('<svg class="connector-icon"')
        assert "<path" in icon


def test_connector_icon_falls_back_to_a_generic_glyph_for_an_unknown_connector() -> None:
    icon = _connector_icon("some_future_connector")

    assert "connector-icon-generic" in icon
    assert "<path" in icon


def test_connector_icon_returns_the_real_browseros_mark_not_the_generic_fallback() -> None:
    icon = _connector_icon("browseros")

    assert "connector-icon-browseros" in icon
    assert "connector-icon-generic" not in icon
    assert "<path" in icon


def test_connectors_page_always_includes_a_live_browseros_row(tmp_path: Path) -> None:
    # No sync_state rows at all -- browseros isn't sync_state-derived, so it
    # must still show up even when every other connector is empty.
    database = Database(tmp_path / "alfred.db")
    client = _client(database)
    _login(client)

    response = client.get("/connectors")

    assert "browseros" in response.text
    assert "127.0.0.1:9200" in response.text
    # Whatever's actually listening on 9200 in the test environment, the
    # row must resolve to one of the two states this probe can produce.
    assert ">Connected<" in response.text or ">Disconnected<" in response.text


def test_rate_filter_distinguishes_unmeasured_from_zero() -> None:
    # A fresh install has not scored 0%, it has not been scored at all.
    assert _rate(None) == "—"
    assert _rate(0.0) == "0%"
    assert _rate(1.0) == "100%"
    assert _rate(0.666) == "67%"


def test_evaluation_page_renders_with_no_feedback_yet(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))
    _login(client)

    response = client.get("/evaluation")

    assert response.status_code == 200
    assert "Evaluation" in response.text
    assert "nothing to attribute" in response.text


def test_evaluation_page_shows_rates_and_source_attribution(tmp_path: Path) -> None:
    from alfred.response_feedback import ResponseFeedbackService

    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            for index, outcome in enumerate(["helpful", "helpful", "wrong_context", "helpful"]):
                response_id = str(700 + index)
                ResponseFeedbackService.record_context_in_transaction(
                    connection,
                    response_update_id=response_id,
                    sources=["gmail"],
                    freshness={"gmail": None},
                    items=[],
                )
                ResponseFeedbackService.record_feedback_in_transaction(
                    connection,
                    callback_query_id=f"callback-{index}",
                    feedback_update_id=str(800 + index),
                    response_update_id=response_id,
                    outcome=outcome,
                )
    client = _client(database)
    _login(client)

    response = client.get("/evaluation")

    assert "75%" in response.text  # three helpful of four votes
    assert "gmail" in response.text
    assert "nothing to attribute" not in response.text


def test_audit_page_shows_redacted_records_never_raw_content(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            AuditLog.append_in_transaction(
                connection,
                AuditEvent(actor="nico", client="cli", tool="task_upsert", outcome="ok", result={"task_id": "abc"}),
            )
    client = _client(database)
    _login(client)

    response = client.get("/audit")

    assert "task_upsert" in response.text
    assert "abc" in response.text  # the redacted result IS shown -- audit records never hold raw secrets to begin with
    assert ">Success<" in response.text  # outcome="ok" renders as the human label, not raw "ok"


def test_outcome_label_covers_every_outcome_string_actually_used_in_the_codebase() -> None:
    """AuditEvent.outcome is a plain str, not a closed enum -- every value any
    caller across the codebase actually produces (jobs, gmail_inbound, models,
    slack, telegram_runtime, ...) must get a real label, not silently fall
    through to a raw, un-styled pill."""
    from alfred.admin_ui import _outcome_label

    for outcome, expected in {
        "ok": "Success",
        "sent": "Sent",
        "handled": "Handled",
        "outbox_enqueued": "Queued",
        "duplicate": "Duplicate",
        "ignored": "Ignored",
        "error": "Error",
        "failed": "Failed",
        "rejected": "Rejected",
        "refused": "Refused",
    }.items():
        assert _outcome_label(outcome) == expected


def test_outcome_label_falls_back_to_title_case_for_an_unknown_outcome() -> None:
    from alfred.admin_ui import _outcome_label

    assert _outcome_label("some_new_outcome") == "Some New Outcome"


def test_audit_page_shows_the_empty_state_with_no_records(tmp_path: Path) -> None:
    client = _client(Database(tmp_path / "alfred.db"))
    _login(client)

    response = client.get("/audit")

    assert "No audit records yet" in response.text


def test_format_dt_handles_none() -> None:
    assert _format_dt(None) is None


def test_format_dt_is_human_readable_not_raw_isoformat() -> None:
    formatted = _format_dt(datetime(2026, 8, 11, 18, 47, 26, 552072, tzinfo=UTC))

    assert "552072" not in formatted  # no raw microseconds
    assert "2026" in formatted


def test_result_preview_truncates_long_results() -> None:
    long_result = {"key": "x" * 200}

    preview = _result_preview(long_result, max_length=20)

    assert len(preview) == 20
    assert preview.endswith("…")


def test_run_admin_ui_defaults_to_loopback_but_accepts_another_host(tmp_path: Path) -> None:
    """127.0.0.1 is the safe default; a VPN/Tailscale IP must still be
    reachable for the documented phone-access path to actually work."""
    database = Database(tmp_path / "alfred.db")
    uvicorn_mock = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"uvicorn": uvicorn_mock}):
        run_admin_ui(database, port=8200, bearer_token_value=TOKEN)
        run_admin_ui(database, port=8200, bearer_token_value=TOKEN, host="100.64.1.2")

    hosts = [call.kwargs.get("host") for call in uvicorn_mock.Config.call_args_list]
    assert hosts == ["127.0.0.1", "100.64.1.2"]
