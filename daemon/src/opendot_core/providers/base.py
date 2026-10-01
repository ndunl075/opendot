"""The provider interface."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from .types import ChatRequest, ModelInfo, StreamEvent


@runtime_checkable
class Provider(Protocol):
    """One way of reaching a model.

    ``name`` is the registry key (``chatgpt_plan``, ``openai_key``,
    ``anthropic_key``, ``openrouter``, ``local``). ``paid`` is True when a
    request can cost real money per token; only ``chatgpt_plan`` (plan usage)
    and ``local`` are not.
    """

    name: str
    paid: bool

    def list_models(self) -> list[ModelInfo]:
        """Fetch the account's model catalog. Raises a ``ProviderError`` on failure."""
        ...

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        """Yield text deltas and tool calls, then exactly one ``Completed``.

        A request is successful only once ``Completed`` was yielded. If the
        stream ends first, raise ``IncompleteResponse``.
        """
        ...
