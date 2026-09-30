"""``openrouter``: one OpenRouter key, many models (pay per token, opt-in).

OpenAI-compatible Chat Completions at openrouter.ai. The key comes from the
keychain entry ``openrouter_api_key``.
"""

from __future__ import annotations

from ._openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    paid = True
    default_base_url = "https://openrouter.ai/api/v1"
    secret_name = "openrouter_api_key"
    extra_payload = {"usage": {"include": True}}

    def __init__(self, **kwargs) -> None:
        kwargs.pop("base_url", None)
        super().__init__(**kwargs)

    def _headers(self, key):
        headers = super()._headers(key)
        headers["X-Title"] = "OpenDot"
        return headers
