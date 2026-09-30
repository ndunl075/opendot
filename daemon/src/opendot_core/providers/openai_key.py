"""``openai_key``: the user's own OpenAI API key (pay per token, opt-in).

Uses the Chat Completions streaming API (see ``_openai_compat``). The key comes
from the OS keychain entry ``openai_api_key`` and is sent only as a Bearer
header to api.openai.com. The spend cap defaults to 0 (unusable until set).
"""

from __future__ import annotations

from ._openai_compat import OpenAICompatProvider


class OpenAIKeyProvider(OpenAICompatProvider):
    name = "openai_key"
    paid = True
    default_base_url = "https://api.openai.com/v1"
    secret_name = "openai_api_key"
    sends_reasoning_effort = True

    def __init__(self, **kwargs) -> None:
        kwargs.pop("base_url", None)  # the key only ever goes to OpenAI
        super().__init__(**kwargs)
