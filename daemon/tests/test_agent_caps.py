"""Monthly call and dollar caps, ported from the subprocess-runner tests."""

from __future__ import annotations

from pathlib import Path

from opendot_core.agent.caps import MODEL_CALL_TOOL, UsageCaps
from opendot_core.db import Database


def _count(database: Database) -> int:
    with database.connect() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM tool_runs WHERE tool = ?", (MODEL_CALL_TOOL,)
            ).fetchone()[0]
        )


def test_no_limits_never_refuse(tmp_path: Path) -> None:
    caps = UsageCaps(Database(tmp_path / "opendot.db"))
    for _ in range(3):
        caps.record_call(ok=True, cost_usd=5.0)

    assert caps.refusal() is None


def test_a_monthly_call_cap_is_enforced_and_audited(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    caps = UsageCaps(database, monthly_call_limit=1)

    assert caps.refusal() is None
    caps.record_call(ok=True)
    refused = caps.refusal()

    assert refused is not None
    assert "monthly call limit" in refused
    assert _count(database) == 1


def test_a_billable_failure_counts_against_the_monthly_cap(tmp_path: Path) -> None:
    """A call that reached the provider costs money whether or not a usable
    answer came back, so it has to consume budget. Counting only successes let
    a retry loop bill indefinitely while the counter stood still."""
    database = Database(tmp_path / "opendot.db")
    caps = UsageCaps(database, monthly_call_limit=2)

    caps.record_call(ok=False, detail="agent timed out")
    caps.record_call(ok=False, detail="agent timed out")

    refused = caps.refusal()
    assert refused is not None
    assert "monthly call limit reached" in refused


def test_a_call_that_never_started_consumes_nothing(tmp_path: Path) -> None:
    """Nothing ran and nothing was billed, so a launch failure that records
    nothing cannot burn the month's allowance in a restart loop."""
    database = Database(tmp_path / "opendot.db")
    caps = UsageCaps(database, monthly_call_limit=2)

    for _ in range(4):
        assert caps.refusal() is None

    assert _count(database) == 0


def test_a_dollar_budget_stops_further_turns(tmp_path: Path) -> None:
    """A call count assumes every turn costs the same. Measured turns varied by
    an order of magnitude in tokens, so only a dollar figure bounds spend."""
    caps = UsageCaps(Database(tmp_path / "opendot.db"), monthly_budget_usd=0.05)

    caps.record_call(ok=True, cost_usd=0.02)
    caps.record_call(ok=True, cost_usd=0.02)

    # $0.04 spent; the third turn is still under and runs, taking it to $0.06.
    assert caps.refusal() is None
    caps.record_call(ok=True, cost_usd=0.02)
    refused = caps.refusal()

    assert refused is not None
    assert "monthly budget reached" in refused


def test_a_failed_turn_still_counts_its_cost(tmp_path: Path) -> None:
    """A failed call still billed, which is exactly when accounting matters."""
    caps = UsageCaps(Database(tmp_path / "opendot.db"), monthly_budget_usd=0.05)

    caps.record_call(ok=False, cost_usd=0.03)
    caps.record_call(ok=False, cost_usd=0.03)

    refused = caps.refusal()
    assert refused is not None
    assert "budget reached" in refused


def test_an_unreadable_cost_does_not_fail_the_turn(tmp_path: Path) -> None:
    """A call recorded without a cost contributes nothing rather than raising;
    the call cap still bounds those."""
    caps = UsageCaps(Database(tmp_path / "opendot.db"), monthly_budget_usd=1.0)

    caps.record_call(ok=True, cost_usd=None)

    assert caps.month_to_date_spend_usd() == 0.0
    assert caps.refusal() is None
