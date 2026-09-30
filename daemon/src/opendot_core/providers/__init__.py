"""Model providers (ARCHITECTURE.md sections 6 and 7)."""

from .base import Provider
from .errors import (
    AuthRequired,
    IncompleteResponse,
    PlanNotGranted,
    ProviderDisabled,
    ProviderError,
    ProviderSwitchForbidden,
    ProviderUnavailable,
    RateLimited,
    UnsupportedCapability,
    UsageLimitExceeded,
    UserNotEligible,
)
from .registry import DEFAULT_PROVIDER, ProviderRegistry, ProviderSettings
from .types import (
    ChatRequest,
    Completed,
    InputItem,
    ModelInfo,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "AuthRequired",
    "ChatRequest",
    "Completed",
    "DEFAULT_PROVIDER",
    "IncompleteResponse",
    "InputItem",
    "ModelInfo",
    "PlanNotGranted",
    "Provider",
    "ProviderDisabled",
    "ProviderError",
    "ProviderRegistry",
    "ProviderSettings",
    "ProviderSwitchForbidden",
    "ProviderUnavailable",
    "RateLimited",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolSpec",
    "UnsupportedCapability",
    "Usage",
    "UsageLimitExceeded",
    "UserNotEligible",
]
