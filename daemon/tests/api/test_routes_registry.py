"""The route-module registry that the M4 endpoint work plugs into."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from opendot_core.api.escrow import TokenEscrow
from opendot_core.api.routes import ApiContext
from opendot_core.api.server import create_app
from opendot_core.db import Database
from opendot_core.policy import ApprovalService
from opendot_core.rules import RuleEngine
from tests.api.test_server import FakeLoop

TOKEN = "routes-registry-token"


def _app(tmp_path: Path, modules: list) -> TestClient:
    database = Database(tmp_path / "r.db")
    database.migrate()
    approvals = ApprovalService(database)
    app = create_app(loop=FakeLoop(approvals), approvals=approvals, rules=RuleEngine(database), token=TOKEN,
                     token_escrow=TokenEscrow(), database=database, extras={"x": 1}, route_modules=modules)
    return TestClient(app)


def test_modules_get_the_context_and_stay_behind_the_bearer_check(tmp_path: Path) -> None:
    seen: list[ApiContext] = []

    def module(ctx: ApiContext) -> list:
        seen.append(ctx)

        async def hello(_request):
            return JSONResponse({"ok": ctx.extras["x"]})

        return [Route("/v1/hello", hello, methods=["GET"])]

    client = _app(tmp_path, [module])
    assert seen and seen[0].database is not None and seen[0].hub is not None
    assert client.get("/v1/hello").status_code == 401
    assert client.get("/v1/hello", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"ok": 1}


def test_a_route_registered_twice_is_an_error(tmp_path: Path) -> None:
    def module(_ctx: ApiContext) -> list:
        async def h(_request):
            return JSONResponse({})

        return [Route("/v1/health", h, methods=["GET"])]

    with pytest.raises(ValueError, match="twice"):
        _app(tmp_path, [module])
