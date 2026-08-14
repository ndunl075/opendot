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
