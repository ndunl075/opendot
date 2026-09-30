"""Model providers (ARCHITECTURE.md sections 6 and 7)."""

from .anthropic_key import AnthropicKeyProvider
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
    SpendCapReached,
    UnsupportedCapability,
    UsageLimitExceeded,
    UserNotEligible,
)
from .factory import build_registry
from .features import FEATURES, FeatureSwitches, paid_retry_choices, paid_retry_offered
from .local import LocalProvider
from .openai_key import OpenAIKeyProvider
from .openrouter import OpenRouterProvider
from .registry import DEFAULT_PROVIDER, ProviderRegistry, ProviderSettings
from .spend import SpendTracker
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
    "AnthropicKeyProvider",
    "FEATURES",
    "FeatureSwitches",
    "LocalProvider",
    "OpenAIKeyProvider",
    "OpenRouterProvider",
    "SpendCapReached",
    "SpendTracker",
    "build_registry",
    "paid_retry_choices",
    "paid_retry_offered",
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
