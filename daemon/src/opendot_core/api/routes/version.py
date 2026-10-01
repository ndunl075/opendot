"""``GET /v1/version``."""

from __future__ import annotations

import os
from importlib import metadata
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..models import API_VERSION, VersionResponse
from ._common import reply

if TYPE_CHECKING:
    from . import ApiContext


def daemon_version() -> str:
    try:
        return metadata.version("opendot-core")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def routes(ctx: ApiContext) -> list[Route]:
    async def version(_: Request) -> Response:
        schema = 0
        if ctx.database is not None:
            schema = int(ctx.database.migrate())
        build = os.environ.get("OPENDOT_BUILD") or None
        return reply(
            VersionResponse(daemon_version=daemon_version(), api_version=API_VERSION, schema_version=schema, build=build)
        )

    return [Route(f"/{API_VERSION}/version", version, methods=["GET"])]
