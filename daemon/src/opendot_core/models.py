"""Local-first text generation, per decision 6: Ollama first, cloud optional.

This is additive, never a replacement: every deterministic pipeline in this
codebase (briefing, jobs, sync) keeps working with zero model calls. A
``TextGenerationProvider`` is opt-in, constructed and passed in explicitly by
a caller that wants it -- nothing here is wired into a default code path,
matching the rule that a model call is never on the default path. That
includes the cloud pieces below: nothing in the CLI or MCP server
constructs a cloud provider or a ``GuardedCloudProvider`` -- an operator
who wants one builds and passes it in from their own configuration.

Decision 6 requires three things of a cloud model specifically: a monthly
hard spend cap that fails closed, redaction of secrets and sensitive content
before egress, and tracking input/output tokens plus estimated cost per run.
``GuardedCloudProvider`` wraps any cloud-compatible ``TextGenerationProvider``
(``OpenAICompatibleClient``, ``AnthropicCompatibleClient``, or a third
party's) with all three; nothing calls a cloud API without going through it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Callable, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database


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


class OpenAICompatibleClient:
    """Cloud fallback speaking OpenAI's chat-completions shape.

    Works against OpenAI itself or any endpoint that mimics that API
    (``base_url`` is the only thing that changes). Never constructed by
    default anywhere in this codebase -- always wrap it in
    ``GuardedCloudProvider`` before handing it to a caller as a
    ``TextGenerationProvider``; using it bare skips the spend cap and
    redaction decision 6 requires.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model_name: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI-compatible API key must not be empty")
        self.model_name = model_name
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.post("/chat/completions", json={"model": self.model_name, "messages": messages})
        if response.status_code >= 400:
            raise ModelError(f"OpenAI-compatible generate failed ({response.status_code}): {response.text}")
        data = response.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelError("OpenAI-compatible response had no choices")
        text = choices[0].get("message", {}).get("content")
        if not isinstance(text, str):
            raise ModelError("OpenAI-compatible response has no message content")
        usage = data.get("usage") or {}
        return GenerationResult(
            text=text,
            model=self.model_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


class AnthropicCompatibleClient:
    """Cloud fallback speaking Anthropic's Messages API shape.

    Same "never construct bare, always wrap in GuardedCloudProvider" rule
    as ``OpenAICompatibleClient``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model_name: str = "claude-sonnet-5",
        base_url: str = "https://api.anthropic.com/v1",
        max_tokens: int = 1024,
        anthropic_version: str = "2023-06-01",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Anthropic-compatible API key must not be empty")
        self.model_name = model_name
        self._max_tokens = max_tokens
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "anthropic-version": anthropic_version},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        payload: dict[str, object] = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        response = self._client.post("/messages", json=payload)
        if response.status_code >= 400:
            raise ModelError(f"Anthropic-compatible generate failed ({response.status_code}): {response.text}")
        data = response.json()
        content = data.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0].get("text"), str):
            raise ModelError("Anthropic-compatible response has no text content")
        text = content[0]["text"]
        usage = data.get("usage") or {}
        return GenerationResult(
            text=text,
            model=self.model_name,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )


# label -> pattern, ordered so a more specific token shape is tried before a
# broad numeric one that could otherwise swallow part of it.
_REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")),
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{10,}\b")),
    ("github_token", re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{10,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
)


class Redactor:
    """Best-effort pattern scrub for secrets/PII before text leaves the process.

    This is not exhaustive -- it catches common, recognizable shapes (API
    keys, emails, phone numbers, card-like digit runs), not every possible
    secret. It exists because decision 6 requires *something* between raw
    local content and a cloud API; treat it as a floor, not a guarantee, and
    keep genuinely `secret`-tagged content out of any prompt in the first
    place rather than relying on this to catch it.
    """

    def redact(self, text: str) -> str:
        redacted = text
        for label, pattern in _REDACTION_PATTERNS:
            redacted = pattern.sub(f"[REDACTED:{label}]", redacted)
        return redacted


class CloudPricing(BaseModel):
    """Operator-supplied cost per 1,000 tokens; this module hardcodes no vendor prices."""

    prompt_usd_per_1k: float
    completion_usd_per_1k: float


class CloudBudgetExceeded(ModelError):
    """Raised instead of calling a cloud provider once the monthly cap is already reached."""


class GuardedCloudProvider:
    """Wrap a cloud ``TextGenerationProvider`` with decision 6's three requirements.

    1. **Redact** the prompt and system text before either ever leaves this
       process.
    2. **Fail closed on budget.** Before calling, this checks whether
       month-to-date spend (summed from this guard's own audit records) has
       already met or exceeded ``monthly_budget_usd``; if so it refuses and
       never calls the provider. The default cap is ``0.0``, so an
       unconfigured guard never calls out at all, matching decision 6's
       "default monthly cloud budget is $0." This checks the cap *before*
       the call, not a per-call ceiling -- a single very large call can
       still land the total over the cap within that one run. A stricter
       prospective per-call estimate is future work.
    3. **Record cost.** Every call, success or failure, is audited with the
       model name, token counts, and estimated USD cost -- never the raw
       prompt or response text -- which doubles as the ledger the budget
       check above reads from.
    """

    tool_name = "cloud_generate"

    def __init__(
        self,
        provider: TextGenerationProvider,
        database: Database,
        *,
        pricing: CloudPricing,
        monthly_budget_usd: float = 0.0,
        redactor: Redactor | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self.database = database
        self._pricing = pricing
        self._monthly_budget_usd = monthly_budget_usd
        self._redactor = redactor or Redactor()
        self._now = now

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        spent = self.month_to_date_spend_usd()
        if spent >= self._monthly_budget_usd:
            self._audit(outcome="refused", cost_usd=0.0, prompt_tokens=None, completion_tokens=None)
            raise CloudBudgetExceeded(
                f"monthly cloud budget (${self._monthly_budget_usd:.2f}) already reached: ${spent:.2f} spent this month"
            )
        redacted_prompt = self._redactor.redact(prompt)
        redacted_system = self._redactor.redact(system) if system else None
        try:
            result = self._provider.generate(redacted_prompt, system=redacted_system)
        except Exception:
            self._audit(outcome="error", cost_usd=0.0, prompt_tokens=None, completion_tokens=None)
            raise
        cost = self._estimate_cost_usd(result.prompt_tokens, result.completion_tokens)
        self._audit(
            outcome="ok", cost_usd=cost, prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens
        )
        return result

    def month_to_date_spend_usd(self) -> float:
        """Sum this guard's own audited cost for the current UTC calendar month."""
        self.database.migrate()
        month_prefix = self._now().strftime("%Y-%m")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM tool_runs WHERE tool = ? AND occurred_at LIKE ?",
                (self.tool_name, f"{month_prefix}%"),
            ).fetchall()
        return sum(json.loads(row["result_json"]).get("cost_usd", 0.0) for row in rows)

    def _estimate_cost_usd(self, prompt_tokens: int | None, completion_tokens: int | None) -> float:
        prompt_cost = (prompt_tokens or 0) / 1000 * self._pricing.prompt_usd_per_1k
        completion_cost = (completion_tokens or 0) / 1000 * self._pricing.completion_usd_per_1k
        return prompt_cost + completion_cost

    def _audit(
        self, *, outcome: str, cost_usd: float, prompt_tokens: int | None, completion_tokens: int | None
    ) -> None:
        AuditLog(self.database).append(
            AuditEvent(
                actor=f"cloud:{self._provider.model_name}",
                client="models",
                tool=self.tool_name,
                outcome=outcome,
                result={
                    "model": self._provider.model_name,
                    "cost_usd": round(cost_usd, 6),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )
        )
