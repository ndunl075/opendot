"""Sign in with ChatGPT: start, status, disconnect (ARCHITECTURE.md section 6.1).

Drives the ``chatgpt_plan`` provider's real flow. ``begin_sign_in`` opens a 127.0.0.1 callback
listener and returns the authorize URL; the UI opens it in the system browser. A background thread
waits for the callback (``complete_sign_in``), which exchanges the code, checks the plan scope and
stores the tokens in the OS keychain. No token ever reaches a response.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...providers.errors import PlanNotGranted, ProviderError, UserNotEligible
from ..models import (
    API_VERSION,
    ChatGPTSignInStart,
    ChatGPTSignInStartRequest,
    ChatGPTStatus,
    OkResponse,
)
from ._common import error, invalid, read_body, reply, utcnow

if TYPE_CHECKING:
    from . import ApiContext

PROVIDER = "chatgpt_plan"
MANAGE_USAGE_URL = "https://chatgpt.com/#settings/Usage"
SIGN_IN_TIMEOUT_SECONDS = 300
_PLAN_LABEL = "Using ChatGPT plan"


class SignInManager:
    """Owns the one in-flight sign-in and the outcome of the last one."""

    def __init__(self, registry: Any, *, timeout: float = SIGN_IN_TIMEOUT_SECONDS) -> None:
        self.registry = registry
        self.timeout = timeout
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._start: ChatGPTSignInStart | None = None
        self._failure: ProviderError | None = None

    def provider(self) -> Any:
        return self.registry.get(PROVIDER)

    def start(self) -> ChatGPTSignInStart:
        provider = self.provider()
        if not callable(getattr(provider, "begin_sign_in", None)):
            raise ProviderError("This provider does not support sign-in.")
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._start is not None:
                return self._start  # a sign-in is already waiting for its browser callback
            begun = provider.begin_sign_in()
            self._failure = None
            started = ChatGPTSignInStart(
                state="pending",
                authorize_url=begun.authorize_url,
                expires_at=utcnow() + timedelta(seconds=self.timeout),
            )
            self._start = started
            thread = threading.Thread(target=self._wait, args=(provider,), daemon=True)
            self._thread = thread
            thread.start()
            return started

    def _wait(self, provider: Any) -> None:
        failure: ProviderError | None = None
        try:
            provider.complete_sign_in(timeout=self.timeout)
        except ProviderError as problem:
            failure = problem
        except Exception as problem:  # never let the thread die silently
            failure = ProviderError(type(problem).__name__)
        with self._lock:
            self._failure = failure

    def pending(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def disconnect(self) -> None:
        provider = self.provider()
        cancel = getattr(provider, "cancel_sign_in", None)
        if callable(cancel):
            cancel()
        thread = self._thread
        if thread is not None:
            thread.join(2.0)  # the cancelled wait records a "cancelled" failure; clear it afterwards
        with self._lock:
            self._failure = None
            self._start = None
        disconnect = getattr(provider, "disconnect", None)
        if not callable(disconnect):
            raise ProviderError("This provider does not support sign-out.")
        disconnect()

    def status(self) -> ChatGPTStatus:
        base = {"manage_usage_url": MANAGE_USAGE_URL}
        try:
            provider = self.provider()
        except ProviderError as problem:
            return ChatGPTStatus(state="error", plan="unknown", eligible=False, error=problem.user_message, **base)
        if self.pending():
            return ChatGPTStatus(state="pending", plan="unknown", eligible=False, **base)
        credits = bool(getattr(provider, "credits_enabled", False))
        status_fn = getattr(provider, "status", None)
        info = status_fn() if callable(status_fn) else None
        failure = self._failure
        if info is not None and info.connected:
            if not info.plan_granted:
                reason = PlanNotGranted().user_message
                return ChatGPTStatus(
                    state="error", plan="ineligible", eligible=False, ineligible_reason=reason, error=reason,
                    account_label=info.email, **base,
                )
            paused = (
                "Paused: the ChatGPT plan usage limit was reached. Raise the limit in ChatGPT, then resume."
                if info.paused
                else None
            )
            # The plan scope is only granted to Plus and Pro, but it does not say which; the contract
            # has no "eligible, tier unknown" value, so a granted plan reports as Plus.
            return ChatGPTStatus(
                state="signed_in", plan="eligible_plus", plan_label=_PLAN_LABEL, eligible=True,
                account_label=info.email, credits_enabled=credits, error=paused, **base,
            )
        if failure is not None:
            ineligible = isinstance(failure, (UserNotEligible, PlanNotGranted))
            return ChatGPTStatus(
                state="error", plan="ineligible" if ineligible else "unknown", eligible=False,
                ineligible_reason=failure.user_message if ineligible else None, error=failure.user_message, **base,
            )
        return ChatGPTStatus(state="signed_out", plan="unknown", eligible=False, **base)


def manager_for(ctx: Any) -> SignInManager | None:
    if ctx.registry is None:
        return None
    manager = ctx.extras.get("chatgpt_signin")
    if manager is None:
        manager = ctx.extras["chatgpt_signin"] = SignInManager(ctx.registry)
    return manager


def current_status(ctx: Any) -> ChatGPTStatus:
    manager = manager_for(ctx)
    if manager is None:
        return ChatGPTStatus(state="signed_out", plan="unknown", eligible=False, manage_usage_url=MANAGE_USAGE_URL)
    return manager.status()


def routes(ctx: ApiContext) -> list[Route]:
    manager = manager_for(ctx)
    if manager is None:
        return []

    async def start(request: Request) -> Response:
        try:
            await read_body(request, ChatGPTSignInStartRequest)  # open_browser: the UI opens the URL itself
        except ValidationError as problem:
            return invalid(problem)
        try:
            started = await asyncio.to_thread(manager.start)
        except ProviderError as problem:
            return error(409, problem.code, problem.user_message if not problem.detail else problem.detail)
        return reply(started)

    async def status(_: Request) -> Response:
        return reply(await asyncio.to_thread(manager.status))

    async def disconnect(_: Request) -> Response:
        try:
            await asyncio.to_thread(manager.disconnect)
        except ProviderError as problem:
            return error(409, problem.code, problem.user_message)
        return reply(OkResponse(message="Signed out of ChatGPT."))

    v = f"/{API_VERSION}/auth/chatgpt"
    return [
        Route(f"{v}/start", start, methods=["POST"]),
        Route(f"{v}/status", status, methods=["GET"]),
        Route(f"{v}/disconnect", disconnect, methods=["POST"]),
    ]
