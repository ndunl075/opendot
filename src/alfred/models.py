"""Local-first text generation, per decision 6: Ollama first, cloud optional.

This is additive, never a replacement: every deterministic pipeline in this
codebase (briefing, jobs, sync) keeps working with zero model calls. A
``TextGenerationProvider`` is opt-in, constructed and passed in explicitly by
a caller that wants it -- nothing here is wired into a default code path,
matching the rule that a model call is never on the default path.

What is NOT built yet: a cloud fallback provider, the monthly cloud-spend cap,
and sensitive-data redaction before egress. Decision 6 requires all three for
cloud models specifically; there is no cloud caller yet for them to guard.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel


class ModelError(RuntimeError):
    """Raised when a local model call fails."""


class GenerationResult(BaseModel):
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class TextGenerationProvider(Protocol):
    """Anything that turns a prompt into text for one named model."""

    model_name: str

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult: ...


class OllamaClient:
    """Default local-first provider backed by a locally running Ollama."""

    def __init__(
        self,
        *,
        model_name: str = "llama3.2",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_name = model_name
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout), transport=transport)

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        """Call Ollama's non-streaming generate endpoint once."""
        payload: dict[str, object] = {"model": self.model_name, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        response = self._client.post("/api/generate", json=payload)
        if response.status_code >= 400:
            raise ModelError(f"Ollama generate failed ({response.status_code}): {response.text}")
        data = response.json()
        text = data.get("response")
        if not isinstance(text, str):
            raise ModelError("Ollama response has no 'response' text field")
        return GenerationResult(
            text=text,
            model=self.model_name,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )
