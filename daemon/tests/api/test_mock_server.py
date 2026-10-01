from __future__ import annotations

import argparse

import pytest
from pydantic import TypeAdapter
from starlette.testclient import TestClient

from opendot_core.api.contract import ENDPOINTS, STREAM
from opendot_core.api.events import StreamEvent
from opendot_core.api.fake_data import fake_json, fake_model
from opendot_core.api.mock_server import canned_chat, create_app, run_check, run_mock_server
from opendot_core.cli import main


def test_fake_data_is_valid_and_deterministic() -> None:
    for ep in ENDPOINTS:
        first = fake_json(ep.response)
        ep.response.model_validate(first)
        assert first == fake_json(ep.response)
        if ep.request is not None:
            ep.request.model_validate(fake_model(ep.request).model_dump(mode="json"))


def test_fake_data_tells_a_coherent_story() -> None:
    from opendot_core.api import models as m

    status = fake_model(m.ChatGPTStatus)
    assert status.eligible and status.state == "signed_in"
    conns = fake_model(m.ConnectionList).connections
    assert {c.health for c in conns} == {"ok", "stale", "error", "never_synced"}
    rules = fake_model(m.RuleList).rules
    assert {r.behavior for r in rules} == {"auto", "auto_if_preapproved", "ask", "handoff"}
    assert any(r.locked and r.core_deny for r in rules)


def test_every_endpoint_serves_its_response_model() -> None:
    client = TestClient(create_app())
    for ep in ENDPOINTS:
        path = ep.path.replace("{", "").replace("}", "")
        kwargs: dict = {}
        if ep.request is not None:
            sample = fake_model(ep.request).model_dump(mode="json", exclude_none=True)
            kwargs["json" if ep.request_in == "body" else "params"] = sample
        response = client.request(ep.method, path, **kwargs)
        assert response.status_code == 200, (ep.name, response.text)
        ep.response.model_validate_json(response.content)


def test_invalid_request_is_rejected() -> None:
    client = TestClient(create_app())
    response = client.post("/v1/rules", json={"name": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert client.get("/v1/usage", params={"days": "0"}).status_code == 422


def test_canned_chat_order() -> None:
    events = canned_chat()
    kinds = [e["type"] for e in events]
    assert kinds[0] == "message_started" and kinds[-1] == "completed"
    assert kinds.index("text_delta") < kinds.index("approval_required") < kinds.index("completed")
    assert [e["seq"] for e in events] == list(range(len(kinds)))
    adapter = TypeAdapter(StreamEvent)
    for event in events:
        adapter.validate_python(event)


def test_websocket_streams_canned_chat() -> None:
    client = TestClient(create_app())
    with client.websocket_connect(STREAM.path) as ws:
        ws.send_json({"type": "send", "text": "hello"})
        kinds: list[str] = []
        while "completed" not in kinds:
            kinds.append(ws.receive_json()["type"])
        assert "approval_required" in kinds
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["code"] == "invalid_frame"
        last_seq = len(canned_chat()) - 1
        ws.send_json({"type": "resume", "conversation_id": "conv_demo", "after_seq": last_seq - 1})
        assert ws.receive_json()["type"] == "completed"


def test_refuses_non_loopback_host(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mock-server", "--host", "0.0.0.0"]) == 2
    assert "127.0.0.1" in capsys.readouterr().err
    assert run_mock_server(argparse.Namespace(host="localhost", port=1, check=False)) == 2


def test_check_exits_zero_and_lists_endpoints(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_check() == 0
    out = capsys.readouterr().out
    assert out.count("OK   ") == len(ENDPOINTS) + 1
    assert "FAIL" not in out


def test_cli_check_flag() -> None:
    assert main(["mock-server", "--check"]) == 0


def test_mock_chat_socket_selects_the_opendot_subprotocol() -> None:
    from opendot_core.api.mock_server import create_app

    with TestClient(create_app()).websocket_connect("/v1/chat/stream", subprotocols=["opendot", "opendot.bearer.x"]) as ws:
        assert ws.accepted_subprotocol == "opendot"
