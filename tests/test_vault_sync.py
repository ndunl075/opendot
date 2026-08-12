import httpx

from alfred.vault_sync import check_couchdb


def test_check_couchdb_reports_reachable_with_its_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/"
        assert "Authorization" not in request.headers  # deliberately unauthenticated
        return httpx.Response(200, json={"couchdb": "Welcome", "version": "3.3.3"})

    status = check_couchdb("http://127.0.0.1:5984", transport=httpx.MockTransport(handler))

    assert (status.reachable, status.couchdb_version, status.error) == (True, "3.3.3", None)


def test_check_couchdb_strips_a_trailing_slash_from_the_base_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"couchdb": "Welcome", "version": "3.3.3"})

    check_couchdb("http://127.0.0.1:5984/", transport=httpx.MockTransport(handler))

    assert seen == ["http://127.0.0.1:5984/"]


def test_check_couchdb_reports_unreachable_on_a_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    status = check_couchdb("http://127.0.0.1:5984", transport=httpx.MockTransport(handler))

    assert status.reachable is False
    assert status.error == "ConnectError"


def test_check_couchdb_reports_unreachable_on_an_http_error_status() -> None:
    status = check_couchdb(
        "http://127.0.0.1:5984", transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down"))
    )

    assert status.reachable is False
    assert status.error == "HTTP 503"


def test_check_couchdb_reports_unreachable_when_the_response_is_not_json() -> None:
    status = check_couchdb(
        "http://127.0.0.1:5984", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))
    )

    assert status.reachable is False
    assert status.error == "response was not JSON"


def test_check_couchdb_reports_unreachable_when_the_payload_is_not_a_couchdb_welcome() -> None:
    """Something answering on that port/path that isn't actually CouchDB
    (a misconfigured reverse proxy, a different service entirely) should
    not be reported as a reachable sync server."""
    status = check_couchdb(
        "http://127.0.0.1:5984", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )

    assert status.reachable is False
    assert status.error == "response was not a CouchDB welcome payload"
