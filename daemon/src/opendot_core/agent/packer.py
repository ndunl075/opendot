"""Cache-friendly prompt packing (ARCHITECTURE.md section 8.2, rules 3, 5 and 7).

Layout, in this exact order::

    1. stable prefix   rules text, persona, tool schemas sorted by name
    2. task summary    rewritten only at compaction
    3. history         append-only, never edited or reordered
    4. changing tail   current time, profile, tasks, memories, the new message

Stable prefix and summary both travel in ``ChatRequest.instructions`` (prefix
first, then ``SUMMARY_MARKER``, then the summary); history is ``input``; the
tail is the final user item. ``prefix_hash`` is the sha256 of the stable prefix
only, so identical persona/rules/tool group give a byte-identical prefix
whatever the time, memories, history or message.

Compaction is a deliberate single cache miss: when history passes a threshold,
the older part is summarized once (cheap model, injected as ``summarize``) and
the summary changes, so the provider's cached prefix is rebuilt once. That one
planned miss is cheaper than a history that keeps growing.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models import Redactor
from ..providers.spend import estimate_tokens
from ..providers.types import ChatRequest, InputItem, ToolSpec
from .redaction import redact_except_current_request

__all__ = [
    "BUDGETS",
    "SUMMARY_MARKER",
    "PackedPrompt",
    "PromptPacker",
    "Tail",
    "assert_prefix_stable",
    "compact",
    "estimate_tokens",
    "needs_compaction",
    "trim_tool_result",
    "trim_to_tokens",
]

#: Section 8.2 rule 7, in tokens.
BUDGETS: dict[str, int] = {
    "persona": 250,
    "profile": 350,
    "tasks": 400,
    "memories": 900,
    "summary": 700,
    "reserve": 400,
}

SUMMARY_MARKER = "\n\n<<task-summary>>\n"
_CURRENT_MESSAGE = "\ncurrent message: "
_TRUNCATED = "[truncated]"
_CHARS_PER_TOKEN = 3  # matches estimate_tokens


def trim_to_tokens(text: str, tokens: int, *, keep: str = "start") -> str:
    """Cut ``text`` to about ``tokens`` tokens, with a marker. ``keep`` is start or end."""
    if estimate_tokens(text) <= tokens:
        return text
    budget = max(0, tokens * _CHARS_PER_TOKEN - len(_TRUNCATED) - 2)
    if keep == "end":
        return f"{_TRUNCATED}\n{text[len(text) - budget :] if budget else ''}"
    return f"{text[:budget]}\n{_TRUNCATED}"


def _fit_lines(items: Sequence[str], tokens: int) -> list[str]:
    """Keep leading items (highest value first) while they fit; shorten the first if none fit."""
    kept: list[str] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item)
        if used + cost <= tokens:
            kept.append(item)
            used += cost
            continue
        remaining = tokens - used
        if remaining > estimate_tokens(_TRUNCATED) + 2 and not kept:
            kept.append(trim_to_tokens(item, remaining))
        break
    return kept


@dataclass(frozen=True)
class Tail:
    """The changing part of a prompt. ``memories`` and ``tasks`` are ordered most valuable first."""

    message: str
    now: str = ""
    memories: Sequence[str] = field(default_factory=tuple)
    profile: str = ""
    tasks: Sequence[str] = field(default_factory=tuple)
    redactor: Redactor | None = None
    """When given, stored third-party text in the tail is scrubbed; the message never is."""


@dataclass(frozen=True)
class PackedPrompt:
    instructions: str
    input: list[InputItem]
    prefix_hash: str
    tools: list[ToolSpec] = field(default_factory=list)

    def to_request(self, model: str, **extra: Any) -> ChatRequest:
        return ChatRequest(
            model=model, instructions=self.instructions, input=list(self.input), tools=list(self.tools), **extra
        )


def _tool_schemas_json(tools: Sequence[ToolSpec]) -> str:
    ordered = sorted(tools, key=lambda tool: tool.name)
    return json.dumps([tool.model_dump() for tool in ordered], sort_keys=True, separators=(",", ":"))


class PromptPacker:
    def __init__(self, budgets: dict[str, int] | None = None) -> None:
        self.budgets = {**BUDGETS, **(budgets or {})}

    def stable_prefix(self, *, persona: str, rules_text: str, tools: Sequence[ToolSpec]) -> str:
        persona = trim_to_tokens(persona, self.budgets["persona"])
        return f"{rules_text}\n\n## Persona\n{persona}\n\n## Tools\n{_tool_schemas_json(tools)}"

    def render_tail(self, tail: Tail) -> str:
        b = self.budgets
        parts: list[str] = []
        if tail.now:
            parts.append(f"Current time: {tail.now}")
        if tail.profile:
            parts.append("Profile:\n" + trim_to_tokens(tail.profile, b["profile"]))
        tasks = _fit_lines(list(tail.tasks), b["tasks"])
        if tasks:
            parts.append("Tasks:\n" + "\n".join(f"- {t}" for t in tasks))
        memories = _fit_lines(list(tail.memories), b["memories"])
        if memories:
            parts.append("Memories:\n" + "\n".join(f"- {m}" for m in memories))
        text = "\n\n".join(parts) + _CURRENT_MESSAGE + tail.message
        if tail.redactor is not None:
            # Scrubs the stored context above the marker, never the owner's message.
            text = redact_except_current_request(text, tail.redactor)
        return text.removeprefix("\n")

    def pack(
        self,
        *,
        persona: str,
        rules_text: str,
        tools: Sequence[ToolSpec],
        summary: str,
        history: Sequence[InputItem],
        tail: Tail,
    ) -> PackedPrompt:
        prefix = self.stable_prefix(persona=persona, rules_text=rules_text, tools=tools)
        summary = trim_to_tokens(summary, self.budgets["summary"], keep="end") if summary else ""
        tail_item = InputItem(role="user", content=self.render_tail(tail))
        return PackedPrompt(
            instructions=prefix + SUMMARY_MARKER + summary,
            input=[*history, tail_item],
            prefix_hash=hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
            tools=sorted(tools, key=lambda tool: tool.name),
        )


def assert_prefix_stable(requests: Sequence[ChatRequest]) -> bool:
    """True iff the stable prefix (instructions before the summary, plus tool schemas) is identical."""

    def stable(request: ChatRequest) -> tuple[str, str]:
        return request.instructions.split(SUMMARY_MARKER, 1)[0], _tool_schemas_json(request.tools)

    if not requests:
        return True
    first = stable(requests[0])
    return all(stable(r) == first for r in requests[1:])


# -- tool result trimming ----------------------------------------------------

_HTML_DROP = re.compile(r"<(script|style|head|noscript|svg)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_BREAK = re.compile(r"</?(p|div|br|li|tr|h[1-6]|table|ul|ol|section|article)\b[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_LOOKS_HTML = re.compile(r"<\s*(html|body|div|p|span|table|a|br|h[1-6]|script|style)\b", re.IGNORECASE)
_EMAIL_KEEP = ("id", "thread_id", "from", "to", "cc", "subject", "date", "snippet", "labels")
_EMAIL_BODY_KEYS = ("body", "text", "html", "body_text", "body_html")


def _html_to_text(text: str) -> str:
    text = _HTML_COMMENT.sub("", _HTML_DROP.sub("", text))
    text = _HTML_TAG.sub("", _HTML_BREAK.sub("\n", text))
    text = html.unescape(text)
    lines = (re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = f"\n{_TRUNCATED} ({len(text) - limit} chars omitted)"
    return text[: max(0, limit)] + marker


def _trim_email(email: dict[str, Any], limit: int) -> str:
    kept = {k: email[k] for k in _EMAIL_KEEP if k in email and email[k] not in (None, "")}
    body = next((email[k] for k in _EMAIL_BODY_KEYS if isinstance(email.get(k), str) and email[k]), "")
    if body and "snippet" not in kept:
        text = _html_to_text(body) if _LOOKS_HTML.search(body) else body
        kept["snippet"] = _cap(" ".join(text.split()), min(300, limit // 2))
    for key, value in list(kept.items()):
        if isinstance(value, str):
            kept[key] = _cap(value, 300)
    return _cap(json.dumps(kept, sort_keys=True, ensure_ascii=False), limit)


def trim_tool_result(result: str | dict[str, Any] | list[Any], limit: int = 2000) -> str:
    """Shrink a tool result before it reaches the model; ``limit`` is in characters.

    HTML becomes extracted text; email-shaped dicts keep headers and a snippet
    (never the full body); lists of emails are trimmed per item; anything cut
    ends with a clear ``[truncated]`` marker.
    """
    if isinstance(result, dict):
        if "subject" in result or "from" in result:
            return _trim_email(result, limit)
        return _cap(json.dumps(result, sort_keys=True, ensure_ascii=False, default=str), limit)
    if isinstance(result, list):
        if result and all(isinstance(item, dict) and ("subject" in item or "from" in item) for item in result):
            per_item = max(200, limit // len(result))
            return _cap("\n".join(_trim_email(item, per_item) for item in result), limit)
        return _cap(json.dumps(result, sort_keys=True, ensure_ascii=False, default=str), limit)
    text = _html_to_text(result) if _LOOKS_HTML.search(result) else result
    return _cap(text, limit)


# -- compaction --------------------------------------------------------------


def _history_tokens(history: Sequence[InputItem]) -> int:
    return sum(estimate_tokens(item.content) + estimate_tokens(item.tool_arguments or "") for item in history)


def needs_compaction(history: Sequence[InputItem], threshold_tokens: int) -> bool:
    return _history_tokens(history) > threshold_tokens


def compact(
    history: Sequence[InputItem],
    summarize: Callable[[str], str],
    *,
    keep_recent: int = 6,
    prior_summary: str = "",
) -> tuple[str, list[InputItem]]:
    """Summarize the older part of ``history`` once; keep the recent turns verbatim.

    Returns ``(summary, kept_history)``. ``summarize`` is the cheap-model call
    (text in, text out). This is the one planned cache miss per threshold
    crossing; the kept turns are the original items, unedited and in order.
    A tool result is never separated from the assistant turn that asked for it.
    """
    items = list(history)
    cut = max(0, len(items) - keep_recent)
    while 0 < cut < len(items) and items[cut].role == "tool":
        cut -= 1
    older, kept = items[:cut], items[cut:]
    if not older:
        return prior_summary, kept
    lines = [f"{item.role}: {item.content}" for item in older]
    transcript = ("Earlier summary:\n" + prior_summary + "\n\n" if prior_summary else "") + "\n".join(lines)
    return summarize(transcript), kept
