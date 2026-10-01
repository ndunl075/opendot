"""The M2 scenario suite (S1-S10) run through the real runner against the real agent loop."""

from __future__ import annotations

import pytest

from opendot_core.eval import runner


def _loop_importable() -> bool:
    try:
        import opendot_core.agent.loop  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _loop_importable(), reason="opendot_core.agent.loop is not built yet")
def test_core_suite_passes() -> None:
    results = runner.run_scenarios(runner.select("core"))
    assert [r.id for r in results] == [f"S{n}" for n in range(1, 11)]
    failures = [runner.format_result(r) for r in results if not r.passed]
    assert failures == []
