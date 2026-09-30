from __future__ import annotations

import argparse
import io
import sys
import types
from datetime import date
from typing import Iterator

from opendot_core.cli import main
from opendot_core.measure import cli as measure_cli
from opendot_core.measure.fake import FakeProvider
from opendot_core.measure.plan import (
    FAIL,
    PASS,
    UNKNOWN,
    MeasureContext,
    detect_caching,
    local_credit_spend,
    run_plan,
)
from opendot_core.providers.errors import AuthRequired
from opendot_core.providers.types import ChatRequest, ModelInfo, StreamEvent, Usage

SECTIONS = [
    "## 1. Model catalog and families",
    "## 2. Automatic prompt caching",
    "## 3. reasoning.effort",
    "## 4. Structured output (JSON Schema)",
    "## 5. WebSocket mode vs resending history over HTTP",
    "## 6. Credit spending in usage data",
]


def ns(**overrides) -> argparse.Namespace:
    base = dict(
        provider="chatgpt_plan",
        out=None,
        max_calls=12,
        model=None,
        allow_paid=False,
        provider_factory=None,
        dry_run=True,
        fake_astra=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def run_cli(args, day=date(2026, 1, 2)):
    out, err = io.StringIO(), io.StringIO()
    code = measure_cli.run(args, clock=lambda: day, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_dry_run_stdout_has_all_sections_and_exit_zero():
    code, out, _ = run_cli(ns())
    assert code == 0
    for heading in SECTIONS:
        assert heading in out
    assert "DRY RUN" in out
    assert "Date: 2026-01-02" in out
    assert "PASS" in out


def test_dry_run_does_not_touch_default_file(tmp_path):
    before = measure_cli.default_out_path().read_text(encoding="utf-8")
    run_cli(ns())
    assert measure_cli.default_out_path().read_text(encoding="utf-8") == before


def test_dry_run_out_writes_file(tmp_path):
    target = tmp_path / "m.md"
    code, _, _ = run_cli(ns(out=str(target), fake_astra=True))
    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert "fake-astra" in text and "Astra appears" in text


def test_real_main_dry_run_exit_zero(capsys):
    assert main(["measure", "--dry-run"]) == 0
    assert "## 2. Automatic prompt caching" in capsys.readouterr().out


def usage(inp=0, cached=0, raw=None):
    return Usage(input_tokens=inp, cached_input_tokens=cached, raw=raw or {})


def test_detect_caching():
    assert detect_caching(usage(3000), usage(3000, 2800))[0] == PASS
    assert detect_caching(usage(3000, raw={"cached_tokens": 0}), usage(3000, raw={"cached_tokens": 0}))[0] == FAIL
    assert detect_caching(usage(3000), usage(3000))[0] == UNKNOWN
    assert detect_caching(usage(100), usage(100))[0] == UNKNOWN


def test_caching_off_reports_fail():
    ctx = MeasureContext(provider=FakeProvider(caching=False))
    results = {r.key: r for r in run_plan(ctx)}
    assert results["caching"].status == FAIL
    assert results["caching"].evidence["call_2_cached_input_tokens"] == 0


def test_caching_on_reports_pass_with_evidence():
    results = {r.key: r for r in run_plan(MeasureContext(provider=FakeProvider()))}
    assert results["caching"].status == PASS
    assert results["caching"].evidence["call_2_cached_input_tokens"] > 0
    assert results["caching"].evidence["call_1_input_tokens"] >= 2000
    assert results["effort"].status == PASS
    assert results["structured"].status == PASS
    assert results["websocket"].status == PASS
    assert results["catalog"].status == PASS


def test_structured_rejected_is_fail():
    results = {r.key: r for r in run_plan(MeasureContext(provider=FakeProvider(structured=False)))}
    assert results["structured"].status == FAIL
    assert results["structured"].evidence["error_code"] == "unsupported_capability"


def test_no_websocket_is_unknown_with_reason():
    results = {r.key: r for r in run_plan(MeasureContext(provider=FakeProvider(websocket=False)))}
    assert results["websocket"].status == UNKNOWN
    assert "no WebSocket capability" in results["websocket"].summary


def test_credit_spend_detection_and_warning(tmp_path):
    assert local_credit_spend(usage(raw={"credits_used": 3})) == ["credits_used"]
    assert local_credit_spend(usage(raw={"credits_used": 0, "billing": {"x": None}})) == []
    results = {r.key: r for r in run_plan(MeasureContext(provider=FakeProvider(credits=True)))}
    assert results["credits"].status == FAIL
    assert results["credits"].warning
    clean = {r.key: r for r in run_plan(MeasureContext(provider=FakeProvider()))}
    assert clean["credits"].status == PASS and clean["credits"].warning is None


def test_credit_warning_reaches_report_and_stderr(tmp_path):
    sys.modules["credits_mod2"] = types.SimpleNamespace(make=lambda: FakeProvider(credits=True))
    try:
        target = tmp_path / "r.md"
        code, _, err = run_cli(ns(dry_run=False, provider_factory="credits_mod2:make", out=str(target)))
    finally:
        del sys.modules["credits_mod2"]
    assert code == 0
    assert "WARNING" in err
    assert "CREDIT SPENDING MAY HAVE BEEN DETECTED" in target.read_text(encoding="utf-8")


def test_max_calls_budget():
    provider = FakeProvider()
    calls = []
    original = provider.stream

    def counting(request: ChatRequest) -> Iterator[StreamEvent]:
        calls.append(1)
        return original(request)

    provider.stream = counting  # type: ignore[method-assign]
    ctx = MeasureContext(provider=provider, max_calls=4)
    results = {r.key: r for r in run_plan(ctx)}
    assert ctx.calls <= 4
    assert len(calls) <= 3  # one of the four was the catalog
    assert results["catalog"].status == PASS
    assert results["caching"].status == PASS
    assert results["effort"].status == UNKNOWN
    assert "budget" in results["effort"].summary
    assert results["websocket"].status == UNKNOWN


def test_paid_provider_refused_without_allow_paid(tmp_path):
    sys.modules["paid_mod"] = types.SimpleNamespace(make=lambda: FakeProvider(paid=True))
    try:
        code, _, err = run_cli(ns(dry_run=False, provider_factory="paid_mod:make", out="unused.md"))
        assert code == 2 and "--allow-paid" in err
        code, _, _ = run_cli(
            ns(dry_run=False, provider_factory="paid_mod:make", allow_paid=True, out=str(tmp_path / "p.md"))
        )
        assert code == 0
    finally:
        del sys.modules["paid_mod"]


def test_not_signed_in_fails_clearly():
    class SignedOut(FakeProvider):
        name = "chatgpt_plan"

        def list_models(self) -> list[ModelInfo]:
            raise AuthRequired()

    sys.modules["out_mod"] = types.SimpleNamespace(make=lambda: SignedOut())
    try:
        code, _, err = run_cli(ns(dry_run=False, provider_factory="out_mod:make"))
    finally:
        del sys.modules["out_mod"]
    assert code == 2 and "Sign in" in err


def test_unavailable_provider_fails_clearly():
    code, _, err = run_cli(ns(dry_run=False, provider_factory="no_such_module_xyz:make"))
    assert code == 2 and "Sign in" in err


def test_report_deterministic_except_date():
    _, first, _ = run_cli(ns(), day=date(2026, 1, 2))
    _, second, _ = run_cli(ns(), day=date(2030, 5, 6))
    assert first != second

    def strip(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if not line.startswith("- Date:"))

    assert strip(first) == strip(second)


def test_no_secrets_in_report(tmp_path):
    secret = "sk-" + "a1b2c3d4e5f6" * 4
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"

    class Leaky(FakeProvider):
        def list_models(self) -> list[ModelInfo]:
            return [ModelInfo(id=f"fake-luna-{secret}", family="luna")] + super().list_models()[1:]

    sys.modules["leaky_mod"] = types.SimpleNamespace(make=lambda: Leaky())
    try:
        target = tmp_path / "r.md"
        code, _, _ = run_cli(ns(dry_run=False, provider_factory="leaky_mod:make", out=str(target)))
    finally:
        del sys.modules["leaky_mod"]
    text = target.read_text(encoding="utf-8")
    assert code == 0
    assert secret not in text and jwt not in text
    assert "Bearer" not in text


def test_the_default_real_run_builds_the_plan_provider_directly(monkeypatch) -> None:
    """Review F1: the default path must not require a configured registry."""
    import argparse

    from opendot_core.measure import cli as measure_cli

    sentinel = object()
    monkeypatch.setattr("opendot_core.providers.chatgpt_plan.ChatGPTPlanProvider", lambda: sentinel)
    args = argparse.Namespace(provider_factory=None, provider="chatgpt_plan")

    assert measure_cli.build_provider(args) is sentinel
