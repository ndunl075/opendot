"""Run the scenario suite and format its report."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from .harness import StackUnavailable
from .scenarios import SCENARIOS, ScenarioResult

SUITES = {"core": [f"S{number}" for number in range(1, 11)]}


class UnknownScenario(ValueError):
    """``--only`` named a scenario that is not in the suite."""


def select(suite: str, only: Iterable[str] | None = None) -> list[str]:
    if suite not in SUITES:
        raise UnknownScenario(f"unknown suite {suite!r}; available: {', '.join(sorted(SUITES))}")
    ids = SUITES[suite]
    if not only:
        return list(ids)
    wanted = [item.strip().upper() for item in only if item.strip()]
    unknown = [item for item in wanted if item not in ids]
    if unknown:
        raise UnknownScenario(f"unknown scenario {', '.join(unknown)}; the {suite} suite has {', '.join(ids)}")
    return [scenario_id for scenario_id in ids if scenario_id in wanted]


def run_scenarios(ids: Iterable[str], *, base_dir: Path | None = None) -> list[ScenarioResult]:
    """Run each scenario in its own temporary directory. Raises ``StackUnavailable`` if a module is missing."""
    results: list[ScenarioResult] = []
    for scenario_id in ids:
        scenario = SCENARIOS[scenario_id]
        with tempfile.TemporaryDirectory(prefix=f"opendot-eval-{scenario_id}-", dir=base_dir, ignore_cleanup_errors=True) as tmp:
            results.append(scenario.run(Path(tmp)))
    return results


def format_result(result: ScenarioResult) -> str:
    if result.passed:
        return f"PASS {result.id} {result.title}"
    detail = " ".join(result.detail.split())
    return f"FAIL {result.id} {result.title}: {detail}"


def format_summary(suite: str, results: list[ScenarioResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    return f"{suite} suite: {passed}/{len(results)} passed" + ("" if passed == len(results) else f", {len(results) - passed} failed")


__all__ = [
    "SUITES",
    "StackUnavailable",
    "UnknownScenario",
    "format_result",
    "format_summary",
    "run_scenarios",
    "select",
]
