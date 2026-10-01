"""Contract endpoints beyond chat and approvals, one module per screen area.

Each module exposes ``routes(ctx: ApiContext) -> list[Route]``. ``ROUTE_MODULES`` lists them;
``api.server.create_app`` mounts every route behind the same bearer check. A module that needs
something the context does not have (for example no database in a unit test) returns no routes, so
the contract endpoint keeps answering 501 ``not_implemented`` through ``api.web``.

Handlers validate bodies with the contract models in ``api/models.py`` and return them with
``model_dump(mode="json")``; errors use ``ErrorResponse`` (see ``api.server._error``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.routing import BaseRoute

from ..escrow import TokenEscrow


@dataclass
class ApiContext:
    loop: Any
    approvals: Any
    rules: Any
    escrow: TokenEscrow
    hub: Any
    actor: str = "owner"
    meter: Any = None
    database: Any = None
    registry: Any = None
    extras: dict[str, Any] = field(default_factory=dict)
    """Anything else a module needs (settings store, connectors ...), set by ``build_agent_runtime``."""


RouteModule = Callable[[ApiContext], list[BaseRoute]]

def _connect(ctx: ApiContext) -> list[BaseRoute]:
    from . import connect

    return connect.routes(ctx)


#: Every route module, in mount order. Add yours here (one line per module).
ROUTE_MODULES: list[RouteModule] = [_connect]


def build_routes(ctx: ApiContext, modules: list[RouteModule] | None = None) -> list[BaseRoute]:
    routes: list[BaseRoute] = []
    for module in ROUTE_MODULES if modules is None else modules:
        routes.extend(module(ctx))
    return routes


__all__ = ["ApiContext", "ROUTE_MODULES", "RouteModule", "build_routes"]
