"""``local``: Ollama or any OpenAI-compatible local server (free, opt-in).

Default base URL is ``http://127.0.0.1:11434`` (Ollama); requests go to
``<base>/v1/chat/completions``. The host must be loopback unless the caller
passes ``allow_remote=True`` explicitly, so prompts never leave the machine by
accident. No key is needed; an optional ``local_api_key`` keychain entry is sent
as a Bearer header if present. Not paid, so no spend cap applies and prompts are
not redacted.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from ._openai_compat import OpenAICompatProvider

DEFAULT_LOCAL_URL = "http://127.0.0.1:11434"


def is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalProvider(OpenAICompatProvider):
    name = "local"
    paid = False
    default_base_url = DEFAULT_LOCAL_URL
    secret_name = "local_api_key"
    key_optional = True
    models_path = "/v1/models"

    def __init__(self, *, base_url: str | None = None, allow_remote: bool = False, **kwargs) -> None:
        url = base_url or DEFAULT_LOCAL_URL
        if not allow_remote and not is_loopback_url(url):
            raise ValueError("the local provider only talks to a loopback address unless allow_remote=True")
        super().__init__(base_url=url, **kwargs)

    def _stream_path(self) -> str:
        return "/v1/chat/completions"
