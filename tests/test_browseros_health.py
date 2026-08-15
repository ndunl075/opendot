import socket

from alfred.browseros_health import browseros_health


def test_ok_when_something_is_listening_on_the_port() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    _, port = server.getsockname()
    try:
        result = browseros_health(port=port)
    finally:
        server.close()

    assert result.connector == "browseros"
    assert result.account == f"127.0.0.1:{port}"
    assert result.state == "ok"
    assert result.last_success_at is None
    assert result.last_error is None


def test_error_when_nothing_is_listening() -> None:
    # Bind then immediately release: a genuinely free ephemeral port, not a
    # guess at an unused one, so this can't flake on a busy CI box.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    _, free_port = probe.getsockname()
    probe.close()

    result = browseros_health(port=free_port, timeout=0.2)

    assert result.state == "error"
    assert result.last_success_at is None
    assert "not reachable" in result.last_error


def test_the_port_comes_from_browseros_own_config(tmp_path, monkeypatch) -> None:
    """The installed build serves 9210 while the docs say 9200, so trusting the
    documented constant reports a healthy service as down."""
    import json

    from alfred.browseros_health import browseros_port

    config = tmp_path / "BrowserClaw" / "User Data" / ".browseros" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"ports": {"cdp": 9110, "server": 9210}}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert browseros_port() == 9210


def test_the_documented_default_is_used_when_browseros_is_not_installed(tmp_path, monkeypatch) -> None:
    """Not installed is the ordinary case, not an error a health probe raises."""
    from alfred.browseros_health import BROWSEROS_DEFAULT_PORT, browseros_port

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert browseros_port() == BROWSEROS_DEFAULT_PORT


def test_a_malformed_config_falls_back_instead_of_raising(tmp_path, monkeypatch) -> None:
    from alfred.browseros_health import BROWSEROS_DEFAULT_PORT, browseros_port

    config = tmp_path / "BrowserClaw" / "User Data" / ".browseros" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert browseros_port() == BROWSEROS_DEFAULT_PORT
