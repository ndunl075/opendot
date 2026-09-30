"""Provider error types.

Each error says what happened and what the user can do about it. None of them
is ever a reason to try a different provider: see ``registry.ProviderRegistry``.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class. ``code`` is stable and safe to show; ``detail`` may help debugging."""

    code = "provider_error"
    status: int | None = None
    user_message = "The model provider returned an error."

    def __init__(self, detail: str = "", *, status: int | None = None) -> None:
        super().__init__(detail or self.user_message)
        self.detail = detail
        if status is not None:
            self.status = status


class ProviderDisabled(ProviderError):
    """The provider, or the feature switch that uses it, is off."""

    code = "provider_disabled"
    user_message = "This provider is turned off in Settings."


class AuthRequired(ProviderError):
    code = "auth_required"
    status = 401
    user_message = "Sign in again to continue."


class UsageLimitExceeded(ProviderError):
    """``subscription_sharing_usage_limit_exceeded`` (429): pause every plan request."""

    code = "usage_limit_exceeded"
    status = 429
    user_message = "Your ChatGPT plan usage limit for OpenDot was reached. Open Manage usage to raise it."


class UserNotEligible(ProviderError):
    """``subscription_sharing_user_not_eligible`` (403), for example a Free or Go account."""

    code = "user_not_eligible"
    status = 403
    user_message = "This ChatGPT account can't share plan usage with apps. A Plus or Pro plan is required."


class UnsupportedCapability(ProviderError):
    """``subscription_sharing_unsupported_capability`` (400)."""

    code = "unsupported_capability"
    status = 400
    user_message = "The ChatGPT plan flow does not support something this request asked for."


class PlanNotGranted(ProviderError):
    """Sign-in succeeded but the plan scope was not granted."""

    code = "plan_not_granted"
    user_message = "Signed in, but this account did not grant plan usage. A Plus or Pro plan is required."


class RateLimited(ProviderError):
    """An ordinary rate limit (not the plan-usage limit)."""

    code = "rate_limited"
    status = 429
    user_message = "The provider is rate limiting requests. Try again shortly."


class ProviderUnavailable(ProviderError):
    """Network failure or a 5xx."""

    code = "provider_unavailable"
    user_message = "The provider could not be reached."


class IncompleteResponse(ProviderError):
    """The stream ended without ``response.completed``. Not a success."""

    code = "incomplete_response"
    user_message = "The response ended before it was complete."


class ProviderSwitchForbidden(ProviderError):
    """Code tried to fall back to a different provider after an error."""

    code = "provider_switch_forbidden"
    user_message = "OpenDot never switches model providers on its own."
