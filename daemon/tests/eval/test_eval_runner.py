from __future__ import annotations

import io
from pathlib import Path

import pytest

from opendot_core.cli import main
from opendot_core.eval import runner, scenarios
from opendot_core.eval.cli import run as run_cli
from opendot_core.eval.harness import StackUnavailable
from opendot_core.eval.scenarios import Scenario, ScenarioFailure, ScenarioResult


def test_the_core_suite_is_exactly_s1_to_s10_with_titles() -> None:
    assert runner.select("core") == [f"S{n}" for n in range(1, 11)]
    assert set(scenarios.SCENARIOS) == set(runner.select("core"))
    assert all(s.title for s in scenarios.SCENARIOS.values())


def test_only_filters_and_keeps_suite_order() -> None:
    assert runner.select("core", ["S5", "s3"]) == ["S3", "S5"]
    with pytest.raises(runner.UnknownScenario):
        runner.select("core", ["S11"])
    with pytest.raises(runner.UnknownScenario):
        runner.select("nope")


def test_line_format() -> None:
    assert runner.format_result(ScenarioResult("S1", "A title", True)) == "PASS S1 A title"
    failed = runner.format_result(ScenarioResult("S2", "Other", False, "line one\nline two"))
    assert failed == "FAIL S2 Other: line one line two"
    summary = runner.format_summary("core", [ScenarioResult("S1", "t", True), ScenarioResult("S2", "t", False)])
    assert summary == "core suite: 1/2 passed, 1 failed"


def test_scenario_turns_assertions_into_a_fail_result(tmp_path: Path) -> None:
    def broken(_: Path) -> None:
        raise ScenarioFailure("nope")

    def crashing(_: Path) -> None:
        raise KeyError("boom")

    assert Scenario("X", "t", broken).run(tmp_path) == ScenarioResult("X", "t", False, "nope")
    assert "KeyError" in Scenario("X", "t", crashing).run(tmp_path).detail


def test_missing_modules_give_a_clear_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_: Path) -> None:
        raise StackUnavailable("opendot_core.agent.loop", "not built")

    monkeypatch.setitem(scenarios.SCENARIOS, "S1", Scenario("S1", "t", unavailable))
    out, err = io.StringIO(), io.StringIO()

    class Args:
        suite = "core"
        only = "S1"

    assert run_cli(Args(), out=out, err=err) == 2  # type: ignore[arg-type]
    assert "opendot_core.agent.loop" in err.getvalue()
    assert "PASS" not in out.getvalue()


def test_cli_dispatch_and_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(scenarios.SCENARIOS, "S1", Scenario("S1", "fine", lambda _: None))
    monkeypatch.setitem(
        scenarios.SCENARIOS, "S2", Scenario("S2", "bad", lambda _: (_ for _ in ()).throw(ScenarioFailure("why")))
    )
    assert main(["eval", "--suite", "core", "--only", "S1"]) == 0
    assert capsys.readouterr().out.splitlines() == ["PASS S1 fine", "core suite: 1/1 passed"]
    assert main(["eval", "--only", "S1,S2"]) == 1
    assert capsys.readouterr().out.splitlines() == ["PASS S1 fine", "FAIL S2 bad: why", "core suite: 1/2 passed, 1 failed"]
    assert main(["eval", "--only", "S99"]) == 2
