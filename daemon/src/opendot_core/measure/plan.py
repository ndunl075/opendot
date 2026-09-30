"""The scripted measurements (ARCHITECTURE.md section 8.5).

Everything here codes against the ``Provider`` protocol. Evidence values are
numbers, booleans, error codes and model ids; request content and secrets are
never recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..providers.errors import (
    AuthRequired,
    IncompleteResponse,
    PlanNotGranted,
    ProviderError,
    UsageLimitExceeded,
    UserNotEligible,
)
from ..providers.types import ChatRequest, Completed, InputItem, ModelInfo, Usage

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

FAMILIES = ("luna", "terra", "sol")
#: Errors that mean the run cannot meaningfully continue; the caller reports them.
FATAL_ERRORS = (AuthRequired, PlanNotGranted, UserNotEligible)
#: A prefix shorter than this is not eligible for automatic caching.
MIN_CACHEABLE_TOKENS = 1024
#: Credit-like weights from section 8.1 (input 1, cached input 0.1, output 6).
_CACHED_WEIGHT = 0.1
_OUTPUT_WEIGHT = 6.0

_CREDIT_WORDS = ("credit", "billing", "spend", "balance", "charge", "cost", "price", "dollar", "usd")
_CACHED_KEY = "cached"


class BudgetExceeded(Exception):
    """The ``--max-calls`` budget would be exceeded."""


class Aborted(Exception):
    """A fatal provider error (sign-in missing, plan not granted) stopped the run."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(error.code)
        self.error = error


@dataclass
class Result:
    key: str
    title: str
    status: str = UNKNOWN
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None


@dataclass
class MeasureContext:
    provider: Any
    max_calls: int = 12
    model: str | None = None
    calls: int = 0
    usages: list[Usage] = field(default_factory=list)
    models: list[ModelInfo] = field(default_factory=list)
    stopped: str | None = None
    """Set when a usage-limit error pauses the run; later measurements become UNKNOWN."""

    def need(self, n: int) -> None:
        if self.calls + n > self.max_calls:
            raise BudgetExceeded(f"needs {n} call(s), {self.max_calls - self.calls} left of {self.max_calls}")

    def complete(self, request: ChatRequest, *, websocket: bool = False) -> Completed:
        self.need(1)
        self.calls += 1
        stream = self.provider.stream_websocket(request) if websocket else self.provider.stream(request)
        done: Completed | None = None
        for event in stream:
            if isinstance(event, Completed):
                done = event
        if done is None:
            raise IncompleteResponse("stream ended without completed")
        self.usages.append(done.usage)
        return done


# ---------------------------------------------------------------- helpers


def family_of(model: ModelInfo) -> str | None:
    if model.family:
        return model.family.lower()
    text = f"{model.id} {model.display_name or ''}".lower()
    for word in (*FAMILIES, "astra"):
        if word in text:
            return word
    return None


def filler_text(paragraphs: int = 120) -> str:
    """Deterministic stable prefix, well over 2,000 tokens."""
    lines = [
        f"Reference note {i}: this paragraph is neutral filler used only to make a long, stable prefix; "
        f"it carries no information and repeats identical words so the prefix is byte-for-byte the same."
        for i in range(paragraphs)
    ]
    return "\n".join(lines)


def weighted_cost(usage: Usage) -> float:
    fresh = max(usage.input_tokens - usage.cached_input_tokens, 0)
    return fresh + _CACHED_WEIGHT * usage.cached_input_tokens + _OUTPUT_WEIGHT * usage.output_tokens


def _error_evidence(error: ProviderError) -> dict[str, Any]:
    return {"error_code": error.code, "http_status": error.status}


def _walk_keys(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.append((path, inner))
            found.extend(_walk_keys(inner, path))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found.extend(_walk_keys(inner, f"{prefix}[{index}]"))
    return found


def raw_has_cached_field(usage: Usage) -> bool:
    return any(_CACHED_KEY in path.lower() for path, _ in _walk_keys(usage.raw))


def detect_caching(first: Usage, second: Usage) -> tuple[str, str]:
    """Classify automatic prompt caching from two identical-prefix calls."""
    if second.cached_input_tokens > 0:
        return PASS, f"second call reported {second.cached_input_tokens} cached input tokens"
    if max(first.input_tokens, second.input_tokens) < MIN_CACHEABLE_TOKENS:
        return UNKNOWN, "prefix was shorter than the minimum cacheable size"
    if raw_has_cached_field(first) or raw_has_cached_field(second):
        return FAIL, "usage reports a cached-token field, but it stayed at 0 on the second call"
    return UNKNOWN, "usage data has no cached-token field, so caching cannot be observed"


def local_credit_spend(usage: Usage) -> list[str]:
    """Conservative fallback: key paths in ``usage.raw`` that look like billing and hold a real value."""
    hits: list[str] = []
    for path, value in _walk_keys(usage.raw):
        leaf = path.rsplit(".", 1)[-1].lower()
        if isinstance(value, (dict, list)):
            continue  # children are walked separately
        if any(word in leaf for word in _CREDIT_WORDS) and value not in (None, 0, 0.0, "", False):
            hits.append(path)
    return hits


def credit_spend_hits(usage: Usage) -> list[str]:
    """Use the plan provider's own detector when importable, else the local conservative check."""
    hits = local_credit_spend(usage)
    try:
        from ..providers.chatgpt_plan import credit_spend_detected  # type: ignore[attr-defined]
    except Exception:
        return hits
    try:
        if credit_spend_detected(usage) and not hits:
            hits.append("(flagged by chatgpt_plan.credit_spend_detected)")
    except Exception:
        pass
    return hits


def _request(ctx: MeasureContext, **overrides: Any) -> ChatRequest:
    base: dict[str, Any] = {"model": ctx.model, "input": [InputItem(role="user", content="Reply with one word.")]}
    base.update(overrides)
    return ChatRequest(**base)


# ---------------------------------------------------------------- measurements


def measure_catalog(ctx: MeasureContext) -> Result:
    result = Result("catalog", "Model catalog and families")
    ctx.need(1)
    ctx.calls += 1
    models = ctx.provider.list_models()
    ctx.models = list(models)
    mapping: dict[str, list[str]] = {family: [] for family in (*FAMILIES, "astra")}
    unmapped: list[str] = []
    for model in models:
        family = family_of(model)
        if family:
            mapping[family].append(model.id)
        else:
            unmapped.append(model.id)
    missing = [family for family in FAMILIES if not mapping[family]]
    astra = bool(mapping["astra"])
    result.evidence = {
        "model_count": len(models),
        "luna": mapping["luna"],
        "terra": mapping["terra"],
        "sol": mapping["sol"],
        "astra_present": astra,
        "astra": mapping["astra"],
        "unmapped": unmapped,
    }
    if not models:
        result.status, result.summary = FAIL, "the catalog was empty"
    elif missing:
        result.status = FAIL
        result.summary = f"no model found for: {', '.join(missing)}"
    else:
        result.status = PASS
        result.summary = "luna, terra and sol all map to catalog models"
    if astra:
        result.summary += "; Astra appears (do not route to it until its rate is known)"
    if ctx.model is None:
        preferred = mapping["luna"] or [m.id for m in models]
        ctx.model = preferred[0] if preferred else None
    return result


def measure_caching(ctx: MeasureContext) -> Result:
    result = Result("caching", "Automatic prompt caching")
    ctx.need(2)
    prefix = filler_text()
    tails = ("Tail one: say the word alpha.", "Tail two: say the word beta.")
    usages = []
    for tail in tails:
        done = ctx.complete(_request(ctx, instructions=prefix, input=[InputItem(role="user", content=tail)]))
        usages.append(done.usage)
    first, second = usages
    result.status, result.summary = detect_caching(first, second)
    result.evidence = {
        "filler": "deterministic neutral filler, identical prefix, different short tails",
        "call_1_input_tokens": first.input_tokens,
        "call_1_cached_input_tokens": first.cached_input_tokens,
        "call_2_input_tokens": second.input_tokens,
        "call_2_cached_input_tokens": second.cached_input_tokens,
    }
    return result


def measure_effort(ctx: MeasureContext) -> Result:
    result = Result("effort", "reasoning.effort")
    ctx.need(2)
    prompt = [InputItem(role="user", content="Explain briefly why the sky is blue.")]
    outputs: dict[str, Usage] = {}
    for effort in ("low", "high"):
        try:
            done = ctx.complete(_request(ctx, input=prompt, effort=effort))
        except FATAL_ERRORS + (UsageLimitExceeded,):
            raise
        except ProviderError as error:
            result.status = FAIL
            result.summary = f"effort={effort!r} was rejected"
            result.evidence = {"effort_rejected": effort, **_error_evidence(error)}
            return result
        outputs[effort] = done.usage
    low, high = outputs["low"], outputs["high"]
    result.evidence = {
        "accepted": ["low", "high"],
        "low_output_tokens": low.output_tokens,
        "low_reasoning_tokens": low.reasoning_tokens,
        "high_output_tokens": high.output_tokens,
        "high_reasoning_tokens": high.reasoning_tokens,
    }
    if (high.output_tokens, high.reasoning_tokens) != (low.output_tokens, low.reasoning_tokens):
        result.status = PASS
        result.summary = "both efforts accepted and token counts differ"
    else:
        result.status = UNKNOWN
        result.summary = "both efforts accepted but token counts were identical (one sample per level)"
    return result


def measure_structured(ctx: MeasureContext) -> Result:
    result = Result("structured", "Structured output (JSON Schema)")
    ctx.need(1)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    try:
        done = ctx.complete(
            _request(
                ctx,
                input=[InputItem(role="user", content='Return JSON with an "answer" field.')],
                structured_output=schema,
            )
        )
    except FATAL_ERRORS + (UsageLimitExceeded,):
        raise
    except ProviderError as error:
        result.status = FAIL
        result.summary = "the JSON Schema request was rejected"
        result.evidence = {"schema_accepted": False, **_error_evidence(error)}
        return result
    try:
        parsed = json.loads(done.text)
        valid = isinstance(parsed, dict) and "answer" in parsed
    except (ValueError, TypeError):
        valid = False
    result.evidence = {"schema_accepted": True, "output_is_valid_json_for_schema": valid}
    result.status = PASS if valid else FAIL
    result.summary = "accepted and the output matched the schema" if valid else "accepted but the output did not match"
    return result


def measure_websocket(ctx: MeasureContext) -> Result:
    result = Result("websocket", "WebSocket mode vs resending history over HTTP")
    if not hasattr(ctx.provider, "stream_websocket"):
        result.summary = "the provider has no WebSocket capability (no stream_websocket)"
        result.evidence = {"websocket_capability": False}
        return result
    ctx.need(4)
    prefix = filler_text(40)
    costs: dict[str, float] = {}
    detail: dict[str, Any] = {"websocket_capability": True}
    try:
        for label, websocket in (("http", False), ("websocket", True)):
            q1 = InputItem(role="user", content="Question one: name a color.")
            first = ctx.complete(_request(ctx, instructions=prefix, input=[q1]), websocket=websocket)
            history = [
                q1,
                InputItem(role="assistant", content=first.text),
                InputItem(role="user", content="Question two: name another color."),
            ]
            second = ctx.complete(_request(ctx, instructions=prefix, input=history), websocket=websocket)
            costs[label] = weighted_cost(second.usage)
            detail[f"{label}_turn2_input_tokens"] = second.usage.input_tokens
            detail[f"{label}_turn2_cached_input_tokens"] = second.usage.cached_input_tokens
            detail[f"{label}_turn2_output_tokens"] = second.usage.output_tokens
            detail[f"{label}_turn2_weighted_cost"] = round(costs[label], 2)
    except FATAL_ERRORS + (UsageLimitExceeded, BudgetExceeded):
        raise
    except ProviderError as error:
        result.status = FAIL
        result.summary = "a WebSocket or HTTP call failed"
        result.evidence = {**detail, **_error_evidence(error)}
        return result
    except Exception as error:  # an unknown transport contract is "unknown", not a failure
        result.status = UNKNOWN
        result.summary = "the WebSocket call did not behave as expected"
        result.evidence = {**detail, "exception_type": type(error).__name__}
        return result
    result.evidence = detail
    if costs["websocket"] < costs["http"]:
        result.status, result.summary = PASS, "turn 2 cost less over WebSocket than over resent HTTP history"
    else:
        result.status, result.summary = FAIL, "WebSocket turn 2 did not cost less than resent HTTP history"
    return result


def measure_credits(ctx: MeasureContext) -> Result:
    result = Result("credits", "Credit spending in usage data (section 6.1 point 10)")
    if not ctx.usages:
        result.summary = "no usage data was collected"
        return result
    hits: list[str] = []
    for usage in ctx.usages:
        for hit in credit_spend_hits(usage):
            if hit not in hits:
                hits.append(hit)
    result.evidence = {"usage_objects_inspected": len(ctx.usages), "credit_like_fields": hits}
    if hits:
        result.status = FAIL
        result.summary = "usage data contains credit or billing signals"
        result.warning = (
            "CREDIT SPENDING MAY HAVE BEEN DETECTED. Plan requests must not spend purchased credits. "
            "Stop using this flow and check your ChatGPT billing before continuing."
        )
    else:
        result.status = PASS
        result.summary = "no credit or billing fields appeared in any usage object"
    return result


# ---------------------------------------------------------------- driver

_STEPS: list[tuple[str, str, Callable[[MeasureContext], Result]]] = [
    ("catalog", "Model catalog and families", measure_catalog),
    ("caching", "Automatic prompt caching", measure_caching),
    ("effort", "reasoning.effort", measure_effort),
    ("structured", "Structured output (JSON Schema)", measure_structured),
    ("websocket", "WebSocket mode vs resending history over HTTP", measure_websocket),
]


def run_plan(ctx: MeasureContext) -> list[Result]:
    """Run every measurement. Fatal provider errors raise ``Aborted``; everything else is recorded."""
    results: dict[str, Result] = {}
    for key, title, step in _STEPS:
        if ctx.stopped:
            results[key] = Result(key, title, UNKNOWN, ctx.stopped)
            continue
        if key != "catalog" and ctx.model is None:
            results[key] = Result(key, title, UNKNOWN, "no model was available to test with")
            continue
        try:
            results[key] = step(ctx)
        except BudgetExceeded as error:
            results[key] = Result(key, title, UNKNOWN, f"skipped: call budget exhausted ({error})")
        except UsageLimitExceeded as error:
            ctx.stopped = "skipped: the plan usage limit was reached, so the run paused"
            results[key] = Result(key, title, UNKNOWN, ctx.stopped, _error_evidence(error))
        except FATAL_ERRORS as error:
            raise Aborted(error) from error
        except ProviderError as error:
            results[key] = Result(key, title, FAIL, "the provider returned an error", _error_evidence(error))
    results["credits"] = measure_credits(ctx)
    order = [key for key, _, _ in _STEPS] + ["credits"]
    return [results[key] for key in order]
