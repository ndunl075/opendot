import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from alfred.db import Database
from alfred.models import (
    AnthropicCompatibleClient,
    CloudBudgetExceeded,
    CloudPricing,
    GenerationResult,
    GuardedCloudProvider,
    ModelError,
    OllamaClient,
    OpenAICompatibleClient,
    Redactor,
)


def test_generate_returns_text_and_token_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        payload = json.loads(request.read())
        assert payload == {"model": "llama3.2", "prompt": "Summarize: hi", "stream": False}
        return httpx.Response(
            200,
            json={"response": "A short summary.", "prompt_eval_count": 12, "eval_count": 5},
        )

    client = OllamaClient(transport=httpx.MockTransport(handler))
    try:
        result = client.generate("Summarize: hi")
    finally:
        client.close()

    assert result.text == "A short summary."
    assert result.model == "llama3.2"
    assert (result.prompt_tokens, result.completion_tokens) == (12, 5)


def test_generate_includes_a_system_prompt_when_given() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["system"] == "Be concise."
        return httpx.Response(200, json={"response": "ok"})

    client = OllamaClient(transport=httpx.MockTransport(handler))
    try:
        client.generate("hi", system="Be concise.")
    finally:
        client.close()


def test_generate_raises_on_an_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model not found")

    client = OllamaClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError, match="500"):
            client.generate("hi")
    finally:
        client.close()


def test_generate_raises_when_the_response_has_no_text_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True})

    client = OllamaClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError, match="no 'response'"):
            client.generate("hi")
    finally:
        client.close()


def test_openai_compatible_client_sends_chat_messages_and_parses_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.read())
        assert payload == {
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "hi"}],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hello."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    client = OpenAICompatibleClient("test-key", transport=httpx.MockTransport(handler))
    try:
        result = client.generate("hi", system="Be concise.")
    finally:
        client.close()
    assert (result.text, result.model, result.prompt_tokens, result.completion_tokens) == ("Hello.", "gpt-4o-mini", 10, 3)


def test_openai_compatible_client_requires_a_non_empty_key() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        OpenAICompatibleClient("  ")


def test_openai_compatible_client_raises_on_error_response() -> None:
    client = OpenAICompatibleClient("test-key", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    try:
        with pytest.raises(ModelError, match="401"):
            client.generate("hi")
    finally:
        client.close()


def test_anthropic_compatible_client_sends_messages_and_parses_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        payload = json.loads(request.read())
        assert payload == {
            "model": "claude-sonnet-5",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
            "system": "Be concise.",
        }
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "Hello."}], "usage": {"input_tokens": 10, "output_tokens": 3}},
        )

    client = AnthropicCompatibleClient("test-key", transport=httpx.MockTransport(handler))
    try:
        result = client.generate("hi", system="Be concise.")
    finally:
        client.close()
    assert (result.text, result.model, result.prompt_tokens, result.completion_tokens) == (
        "Hello.",
        "claude-sonnet-5",
        10,
        3,
    )


def test_anthropic_compatible_client_raises_on_error_response() -> None:
    client = AnthropicCompatibleClient("test-key", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="down")))
    try:
        with pytest.raises(ModelError, match="500"):
            client.generate("hi")
    finally:
        client.close()


def test_redactor_masks_common_secret_and_pii_shapes() -> None:
    redactor = Redactor()
    cases = {
        "email me at nico@example.com": "[REDACTED:email]",
        "Authorization: Bearer abc123.def-456": "[REDACTED:bearer_token]",
        "key is sk-abcdefghij1234567890": "[REDACTED:openai_api_key]",
        "token ghp_abcdefghij1234567890": "[REDACTED:github_token]",
        "token xoxb-1234567890-abcdefghij": "[REDACTED:slack_token]",
        "aws key AKIAABCDEFGHIJKLMNOP": "[REDACTED:aws_access_key]",
        "ssn 123-45-6789": "[REDACTED:ssn]",
        "card 4111 1111 1111 1111": "[REDACTED:credit_card]",
        "call 555-123-4567": "[REDACTED:phone]",
    }
    for text, expected_marker in cases.items():
        assert expected_marker in redactor.redact(text), text


def test_redactor_leaves_ordinary_text_alone() -> None:
    assert Redactor().redact("Buy milk on Friday.") == "Buy milk on Friday."


class _FakeCloudProvider:
    def __init__(self, *, model_name: str = "fake-model") -> None:
        self.model_name = model_name
        self.calls: list[tuple[str, str | None]] = []

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        self.calls.append((prompt, system))
        return GenerationResult(text="generated", model=self.model_name, prompt_tokens=1000, completion_tokens=500)


_PRICING = CloudPricing(prompt_usd_per_1k=0.01, completion_usd_per_1k=0.02)


def test_guarded_cloud_provider_refuses_with_the_default_zero_budget(tmp_path: Path) -> None:
    provider = _FakeCloudProvider()
    guard = GuardedCloudProvider(provider, Database(tmp_path / "alfred.db"), pricing=_PRICING)

    with pytest.raises(CloudBudgetExceeded, match=r"\$0\.00"):
        guard.generate("hi")
    assert provider.calls == []  # fail closed: the provider is never actually called


def test_guarded_cloud_provider_calls_through_within_budget_and_tracks_cost(tmp_path: Path) -> None:
    provider = _FakeCloudProvider()
    guard = GuardedCloudProvider(provider, Database(tmp_path / "alfred.db"), pricing=_PRICING, monthly_budget_usd=1.0)

    result = guard.generate("hi")

    assert result.text == "generated"
    # 1000 prompt tokens * $0.01/1k + 500 completion tokens * $0.02/1k = $0.02
    assert guard.month_to_date_spend_usd() == pytest.approx(0.02)


def test_guarded_cloud_provider_redacts_before_calling_the_inner_provider(tmp_path: Path) -> None:
    provider = _FakeCloudProvider()
    guard = GuardedCloudProvider(provider, Database(tmp_path / "alfred.db"), pricing=_PRICING, monthly_budget_usd=10.0)

    guard.generate("email me at nico@example.com", system="reply to advisor@school.example")

    prompt, system = provider.calls[0]
    assert "nico@example.com" not in prompt
    assert "[REDACTED:email]" in prompt
    assert system is not None and "[REDACTED:email]" in system


def test_guarded_cloud_provider_stops_once_accumulated_spend_reaches_the_cap(tmp_path: Path) -> None:
    """The cap is checked before each call against spend *so far*, not a
    per-call ceiling (documented, known trade-off) -- so a $0.03 cap with
    $0.02 calls allows two calls (spend $0.02, then $0.04) before the third
    call sees spend already >= cap and refuses."""
    provider = _FakeCloudProvider()
    database = Database(tmp_path / "alfred.db")
    guard = GuardedCloudProvider(provider, database, pricing=_PRICING, monthly_budget_usd=0.03)

    guard.generate("call one")  # spend so far: $0.00 < $0.03 -> allowed; spend becomes $0.02
    guard.generate("call two")  # spend so far: $0.02 < $0.03 -> allowed; spend becomes $0.04
    with pytest.raises(CloudBudgetExceeded):
        guard.generate("call three")  # spend so far: $0.04 >= $0.03 -> refused
    assert len(provider.calls) == 2


def test_guarded_cloud_provider_spend_is_scoped_to_the_current_month(tmp_path: Path) -> None:
    """Spend accounting reads real audit rows scoped by ``occurred_at``, so
    this inserts one directly with a January timestamp -- ``tool_runs`` is
    append-only (an UPDATE would hit its own trigger), and a normal
    ``AuditLog.append()`` always stamps the real wall-clock time, which is
    the thing under test here, not the guard's injectable ``now``."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO tool_runs (
                    id, occurred_at, actor, client, tool, outcome, arguments_json, result_json, record_hash
                ) VALUES ('row-1', '2026-01-15T12:00:00+00:00', 'cloud:fake-model', 'models', 'cloud_generate',
                          'ok', '{}', '{"model":"fake-model","cost_usd":0.05}', 'test-hash-1')
                """
            )

    guard_next_month = GuardedCloudProvider(
        _FakeCloudProvider(), database, pricing=_PRICING, now=lambda: datetime(2026, 2, 1, tzinfo=UTC)
    )
    assert guard_next_month.month_to_date_spend_usd() == pytest.approx(0.0)

    guard_same_month = GuardedCloudProvider(
        _FakeCloudProvider(), database, pricing=_PRICING, now=lambda: datetime(2026, 1, 20, tzinfo=UTC)
    )
    assert guard_same_month.month_to_date_spend_usd() == pytest.approx(0.05)


def test_guarded_cloud_provider_audits_without_the_raw_prompt_or_response(tmp_path: Path) -> None:
    provider = _FakeCloudProvider()
    database = Database(tmp_path / "alfred.db")
    guard = GuardedCloudProvider(provider, database, pricing=_PRICING, monthly_budget_usd=1.0)

    guard.generate("a secret prompt nico@example.com")

    with database.connect() as connection:
        row = connection.execute("SELECT result_json FROM tool_runs WHERE tool = 'cloud_generate'").fetchone()
    payload = json.loads(row["result_json"])
    assert payload["model"] == "fake-model"
    assert payload["prompt_tokens"] == 1000
    assert payload["completion_tokens"] == 500
    assert "a secret prompt" not in row["result_json"]
    assert "generated" not in row["result_json"]


def test_guarded_cloud_provider_audits_and_reraises_on_a_failed_call(tmp_path: Path) -> None:
    class FailingProvider:
        model_name = "fake-model"

        def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
            raise ModelError("upstream is down")

    database = Database(tmp_path / "alfred.db")
    guard = GuardedCloudProvider(FailingProvider(), database, pricing=_PRICING, monthly_budget_usd=1.0)

    with pytest.raises(ModelError, match="upstream is down"):
        guard.generate("hi")

    with database.connect() as connection:
        row = connection.execute("SELECT outcome, result_json FROM tool_runs WHERE tool = 'cloud_generate'").fetchone()
    assert row["outcome"] == "error"
    assert json.loads(row["result_json"])["cost_usd"] == 0.0
    assert guard.month_to_date_spend_usd() == pytest.approx(0.0)
