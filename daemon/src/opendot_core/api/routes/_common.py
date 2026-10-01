"""Small helpers shared by the route modules (kept out of ``api.server`` to avoid an import cycle)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...settings_store import SettingsStore
from ..models import ErrorResponse


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(code=code, message=message).model_dump(mode="json"), status_code=status)


def invalid(problem: ValidationError) -> JSONResponse:
    return error(422, "invalid_request", str(problem.errors()[0]["msg"]))


def reply(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


async def read_body(request: Request, model: type[BaseModel]) -> Any:
    raw = await request.body()
    return model.model_validate_json(raw if raw.strip() else b"{}")


def read_query(request: Request, model: type[BaseModel]) -> Any:
    return model.model_validate(dict(request.query_params))


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def settings_for(ctx: Any) -> SettingsStore | None:
    """The shared settings store (one per app), or None when the app has no database."""
    store = ctx.extras.get("settings")
    if store is None and ctx.database is not None:
        store = ctx.extras["settings"] = SettingsStore(ctx.database)
    return store
