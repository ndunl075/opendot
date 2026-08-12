import json

import httpx
import pytest

from alfred.models import ModelError, OllamaClient


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
