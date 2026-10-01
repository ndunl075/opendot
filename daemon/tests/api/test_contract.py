from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from opendot_core.api import export
from opendot_core.api.contract import ENDPOINTS, STREAM, all_models, event_type
from opendot_core.api.events import ClientFrame, StreamEvent
from opendot_core.api.export import build_files, export_contract, find_repo_root

REPO = find_repo_root(Path(__file__).resolve())
COMMITTED = REPO / "contract"


def test_endpoint_names_and_routes_are_unique() -> None:
    assert len({ep.name for ep in ENDPOINTS}) == len(ENDPOINTS)
    assert len({(ep.method, ep.path) for ep in ENDPOINTS}) == len(ENDPOINTS)


def test_endpoints_are_well_formed() -> None:
    for ep in ENDPOINTS:
        assert ep.method in {"GET", "POST", "PUT", "DELETE"}
        assert ep.path.startswith("/v1/")
        if ep.method in {"GET", "DELETE"}:
            assert ep.request_in in (None, "query")
        if ep.request is not None:
            assert ep.request_in in {"body", "query"}


def test_required_screens_are_covered() -> None:
    paths = {ep.path for ep in ENDPOINTS}
    for needle in (
        "/v1/health",
        "/v1/version",
        "/v1/auth/chatgpt/start",
        "/v1/auth/chatgpt/status",
        "/v1/auth/chatgpt/disconnect",
        "/v1/chat/messages",
        "/v1/approvals",
        "/v1/companion/tasks",
        "/v1/activity",
        "/v1/rules",
        "/v1/memory",
        "/v1/memory/forget",
        "/v1/connections",
        "/v1/usage",
        "/v1/settings",
        "/v1/backup",
        "/v1/backup/restore",
    ):
        assert needle in paths, needle
    assert any(p.endswith("/always-allow") for p in paths)


def test_rule_behaviors_are_the_four_from_the_spec() -> None:
    from opendot_core.api.models import RuleCreateRequest

    schema = RuleCreateRequest.model_json_schema()
    assert schema["properties"]["behavior"]["enum"] == ["auto", "auto_if_preapproved", "ask", "handoff"]


def test_stream_events_are_a_discriminated_union() -> None:
    types = {event_type(m) for m in STREAM.server_events}
    assert types == {"message_started", "text_delta", "tool_call", "approval_required", "completed", "error", "paused"}
    adapter = TypeAdapter(StreamEvent)
    event = adapter.validate_python(
        {"type": "text_delta", "seq": 1, "conversation_id": "c", "message_id": "m", "text": "hi"}
    )
    assert type(event).__name__ == "TextDeltaEvent"
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "nope", "seq": 1, "conversation_id": "c", "message_id": "m"})
    assert "oneOf" in adapter.json_schema()
    assert TypeAdapter(ClientFrame).validate_python({"type": "ping"}).type == "ping"


def test_extra_fields_are_rejected() -> None:
    from opendot_core.api.models import RuleCreateRequest

    with pytest.raises(ValidationError):
        RuleCreateRequest.model_validate({"name": "x", "action": "a", "behavior": "ask", "surprise": 1})


def test_every_registry_model_is_exported() -> None:
    files = build_files()
    for name in all_models():
        assert f"schemas/{name}.json" in files
    for ep in ENDPOINTS:
        for model in (ep.request, ep.response):
            if model is not None:
                assert f"schemas/{model.__name__}.json" in files
    assert "schemas/StreamEvent.json" in files and "schemas/ClientFrame.json" in files
    index = json.loads(files["index.json"])
    assert len(index["endpoints"]) == len(ENDPOINTS)
    assert {e["type"] for e in index["stream"]["server_events"]} == {event_type(m) for m in STREAM.server_events}
    for schema_path in index["schemas"]:
        assert schema_path in files


def test_export_is_deterministic_and_lf_only(tmp_path: Path) -> None:
    first = export_contract(tmp_path / "a")
    export_contract(tmp_path / "b")
    for path in first:
        data = path.read_bytes()
        assert data.endswith(b"\n") and not data.endswith(b"\n\n")
        assert b"\r" not in data
        assert data == (tmp_path / "b" / path.relative_to(tmp_path / "a")).read_bytes()


def test_committed_contract_matches_a_fresh_export(tmp_path: Path) -> None:
    assert COMMITTED.is_dir(), "run `opendot contract export` and commit contract/"
    export_contract(tmp_path)
    fresh = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    committed = {p.relative_to(COMMITTED).as_posix(): p.read_bytes() for p in COMMITTED.rglob("*") if p.is_file()}
    assert sorted(fresh) == sorted(committed), "contract/ has missing or stale files; re-run `opendot contract export`"
    drifted = [name for name in fresh if fresh[name] != committed[name]]
    assert not drifted, f"contract/ drifted from the models; re-run `opendot contract export`: {drifted}"


def test_find_repo_root_walks_up_and_fails_cleanly(tmp_path: Path) -> None:
    assert (find_repo_root(Path(__file__).resolve()) / "ARCHITECTURE.md").is_file()
    with pytest.raises(FileNotFoundError):
        find_repo_root(tmp_path / "nowhere")


def test_cli_export_writes_to_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from opendot_core.cli import main

    assert main(["contract", "export", "--out", str(tmp_path / "c")]) == 0
    assert re.search(r"wrote \d+ files", capsys.readouterr().out)
    assert (tmp_path / "c" / "index.json").is_file()


def test_dump_json_is_sorted_with_trailing_newline() -> None:
    assert export.dump_json({"b": 1, "a": 2}) == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_no_real_personal_data_in_contract() -> None:
    text = "".join(build_files().values())
    assert "@gmail.com" not in text
