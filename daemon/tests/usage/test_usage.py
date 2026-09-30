import json
import sqlite3
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import pytest

from opendot_core.cli import main
from opendot_core.db import Database
from opendot_core.providers.types import Usage
from opendot_core.usage import (
    BudgetExceeded,
    Budgets,
    DailyBudgetExceeded,
    TaskBudgetExceeded,
    UsageMeter,
    load_rate_card,
)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


START = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "opendot.db")
    db.migrate()
    return db


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def meter(database: Database, clock: Clock) -> UsageMeter:
    return UsageMeter(database, clock=clock)


def ten_step_task(card, model: str, *, cached: bool, compacted: bool = False) -> float:
    """The section 8.1 example: 12k context growing 2k per step, 600 output tokens per step."""
    total = 0.0
    for step in range(10):
        context = 12_000 + 2_000 * step
        if compacted:
            context = 6_000 + 500 * step
        cached_tokens = (context - 2_000 if not compacted else context - 500) if cached and step else 0
        if compacted and not step:
            cached_tokens = 0
        total += card.credits(
            model, Usage(input_tokens=context, cached_input_tokens=cached_tokens, output_tokens=600)
        ).credits
    return total


@pytest.mark.parametrize(
    ("model", "expected"),
    [("gpt-5.6-sol", 30.75), ("gpt-5.6-terra", 12.3), ("gpt-5.6-luna", 1.23)],
)
def test_uncached_ten_step_task_matches_the_rate_card_table(model: str, expected: float) -> None:
    assert ten_step_task(load_rate_card(), model, cached=False) == pytest.approx(expected, abs=0.01)


def test_cached_ten_step_task_is_about_a_third_of_uncached() -> None:
    card = load_rate_card()
    sol = ten_step_task(card, "gpt-5.6-sol", cached=True)
    assert 9.5 < sol < 11.5  # table says 10.5
    luna = ten_step_task(card, "gpt-5.6-luna", cached=True)
    assert 0.35 < luna < 0.5  # table says 0.4


def test_cached_fresh_and_output_pricing() -> None:
    card = load_rate_card()
    fresh = card.credits("gpt-5.6-sol", Usage(input_tokens=1_000_000))
    cached = card.credits("gpt-5.6-sol", Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000))
    output = card.credits("gpt-5.6-sol", Usage(output_tokens=1_000_000))
    assert fresh.credits == pytest.approx(125.0)
    assert cached.credits == pytest.approx(12.5)
    assert output.credits == pytest.approx(750.0)
    assert fresh.credits / cached.credits == pytest.approx(10)
    assert output.credits / fresh.credits == pytest.approx(6)
    assert card.credits("luna-mini", Usage(input_tokens=1_000_000)).credits == pytest.approx(5.0)
    assert card.credits("TERRA", Usage(output_tokens=1_000_000)).credits == pytest.approx(300.0)
    # cached cannot exceed input
    over = card.credits("luna", Usage(input_tokens=100, cached_input_tokens=500))
    assert over.credits == pytest.approx(100 * 0.5 / 1e6)
    assert fresh.rate_known and cached.rate_known


def test_model_ratio_is_about_25x() -> None:
    card = load_rate_card()
    usage = Usage(input_tokens=1000, output_tokens=1000)
    assert card.credits("sol", usage).credits / card.credits("luna", usage).credits == pytest.approx(25)


def test_unknown_model_is_charged_at_top_rate_and_flagged(meter: UsageMeter) -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    record = meter.record(task_id="t", job_type="chat", model="mystery-9", effort=None, usage=usage)
    assert record.rate_known is False
    assert record.credits == pytest.approx(875.0)
    known = meter.record(task_id="t", job_type="chat", model="gpt-5.6-luna", effort="low", usage=usage)
    assert known.rate_known is True
    assert meter.summary().unknown_rate_calls == 1


def test_family_match_needs_a_whole_word() -> None:
    card = load_rate_card()
    assert card.family_for("gpt-5.6-luna") == "luna"
    assert card.family_for("absolutely") is None


def test_rate_card_is_packaged_data() -> None:
    assert files("opendot_core.usage").joinpath("rate_card.toml").is_file()


def test_totals_per_task_step_and_day(meter: UsageMeter) -> None:
    usage = Usage(input_tokens=1_000_000)
    first = meter.record(task_id="a", job_type="chat", model="luna", effort="low", usage=usage)
    second = meter.record(task_id="a", job_type="chat", model="luna", effort="low", usage=usage)
    meter.record(task_id="b", job_type="brief", model="terra", effort="medium", usage=usage)
    assert (first.step, second.step) == (1, 2)
    assert meter.credits_for_task("a") == pytest.approx(10.0)
    assert meter.credits_for_task("b") == pytest.approx(50.0)
    assert meter.credits_for_task("none") == 0.0
    assert meter.credits_today() == pytest.approx(60.0)
    records = meter.records_for_task("a")
    assert [r.step for r in records] == [1, 2]
    assert records[0].effort == "low"


def test_day_rolls_over_at_utc_midnight(meter: UsageMeter, clock: Clock) -> None:
    usage = Usage(input_tokens=1_000_000)
    clock.now = datetime(2026, 8, 1, 23, 59, tzinfo=UTC)
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    assert meter.credits_today() == pytest.approx(5.0)
    clock.now = datetime(2026, 8, 2, 0, 1, tzinfo=UTC)
    assert meter.credits_today() == 0.0
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    assert meter.credits_today() == pytest.approx(5.0)
    assert meter.credits_for_task("a") == pytest.approx(10.0)


def test_non_utc_clock_is_converted(database: Database) -> None:
    from datetime import timezone

    local = Clock(datetime(2026, 8, 1, 22, 0, tzinfo=timezone(timedelta(hours=-5))))
    assert UsageMeter(database, clock=local).today() == "2026-08-02"


def test_budget_defaults_and_changes(meter: UsageMeter) -> None:
    assert meter.get_budgets() == Budgets(task_credits=5.0, daily_credits=50.0)
    meter.set_budgets(task_credits=8)
    assert meter.get_budgets() == Budgets(task_credits=8, daily_credits=50.0)
    meter.set_budgets(daily_credits=20)
    assert meter.get_budgets() == Budgets(task_credits=8, daily_credits=20)
    with pytest.raises(ValueError):
        meter.set_budgets(task_credits=-1)


def test_budgets_persist_across_instances(database: Database, clock: Clock) -> None:
    UsageMeter(database, clock=clock).set_budgets(task_credits=2, daily_credits=9)
    again = UsageMeter(database, clock=clock, budgets=Budgets(task_credits=100, daily_credits=100))
    assert again.get_budgets() == Budgets(task_credits=2, daily_credits=9)
    fresh_db = Database(database.path.parent / "other.db")
    fresh_db.migrate()
    assert UsageMeter(fresh_db, budgets=Budgets(task_credits=1, daily_credits=3)).get_budgets() == Budgets(
        task_credits=1, daily_credits=3
    )


def test_records_persist_across_instances(database: Database, clock: Clock) -> None:
    UsageMeter(database, clock=clock).record(
        task_id="a", job_type="chat", model="terra", effort="low", usage=Usage(input_tokens=1_000_000)
    )
    again = UsageMeter(database, clock=clock)
    assert again.credits_for_task("a") == pytest.approx(50.0)
    assert again.credits_today() == pytest.approx(50.0)


def test_task_budget_pauses_and_override_continues(meter: UsageMeter) -> None:
    usage = Usage(input_tokens=1_000_000)  # 5 credits on luna
    meter.check_before_request("a")
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    with pytest.raises(TaskBudgetExceeded) as caught:
        meter.check_before_request("a")
    assert isinstance(caught.value, BudgetExceeded)
    assert caught.value.task_id == "a"
    assert caught.value.spent == pytest.approx(5.0)
    meter.check_before_request("other-task")
    meter.allow_task_overrun("a")
    meter.check_before_request("a")
    meter.allow_task_overrun("a")  # idempotent
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    meter.check_before_request("a")


def test_task_overrun_persists_across_instances(database: Database, clock: Clock) -> None:
    first = UsageMeter(database, clock=clock)
    first.record(task_id="a", job_type="chat", model="luna", effort=None, usage=Usage(input_tokens=2_000_000))
    first.allow_task_overrun("a")
    UsageMeter(database, clock=clock).check_before_request("a")


def test_daily_budget_is_a_hard_stop_even_with_task_override(meter: UsageMeter, clock: Clock) -> None:
    meter.set_budgets(task_credits=100, daily_credits=10)
    usage = Usage(input_tokens=1_000_000)
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    meter.check_before_request("a")
    meter.record(task_id="b", job_type="chat", model="luna", effort=None, usage=usage)
    for task in ("a", "b", "brand-new"):
        with pytest.raises(DailyBudgetExceeded):
            meter.check_before_request(task)
    meter.allow_task_overrun("a")
    with pytest.raises(DailyBudgetExceeded):
        meter.check_before_request("a")
    # raising the budget or the next UTC day lifts it
    meter.set_budgets(daily_credits=50)
    meter.check_before_request("a")
    meter.set_budgets(daily_credits=10)
    clock.now += timedelta(days=1)
    meter.check_before_request("a")


def test_daily_takes_precedence_over_task(meter: UsageMeter) -> None:
    meter.set_budgets(task_credits=1, daily_credits=1)
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=Usage(input_tokens=1_000_000))
    with pytest.raises(DailyBudgetExceeded):
        meter.check_before_request("a")


def test_summary_groups_by_task_day_and_job_type(meter: UsageMeter, clock: Clock) -> None:
    usage = Usage(input_tokens=1_000_000)
    clock.now = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)  # outside a 7-day window
    meter.record(task_id="old", job_type="chat", model="luna", effort=None, usage=usage)
    clock.now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    clock.now = START
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=usage)
    meter.record(task_id="b", job_type="brief", model="terra", effort="low", usage=usage)

    summary = meter.summary(days=7)
    assert summary.today == "2026-08-01"
    assert summary.since == "2026-07-26"
    assert summary.total_credits == pytest.approx(60.0)
    assert summary.credits_today == pytest.approx(55.0)
    assert [(line.key, line.credits, line.calls) for line in summary.by_task] == [("b", 50.0, 1), ("a", 10.0, 2)]
    assert [(line.key, line.credits) for line in summary.by_day] == [("2026-07-31", 5.0), ("2026-08-01", 55.0)]
    assert {line.key: line.calls for line in summary.by_job_type} == {"brief": 1, "chat": 2}
    assert summary.budgets == Budgets()
    assert meter.summary(days=30).total_credits == pytest.approx(65.0)


def test_migration_applies_on_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    migrations = sorted(p for p in files("opendot_core.migrations").iterdir() if p.name.endswith(".sql"))
    database = Database(path)
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "filename TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for migration in migrations:
            version = int(migration.name.split("_", maxsplit=1)[0])
            if version >= 19:
                break
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{migration.read_text(encoding='utf-8')}\n"
                f"INSERT INTO schema_migrations (version, filename) VALUES ({version}, '{migration.name}');\n"
                "COMMIT;"
            )
    with database.connect() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 18
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT * FROM credit_records")
    assert database.migrate() >= 19
    meter = UsageMeter(database)
    meter.record(task_id="a", job_type="chat", model="luna", effort=None, usage=Usage(input_tokens=10))
    assert meter.credits_for_task("a") > 0
    assert database.migrate() >= 19


def test_usage_cli_prints_summary_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "opendot.db"
    assert main(["--db", str(path), "init"]) == 0
    capsys.readouterr()
    UsageMeter(Database(path)).record(
        task_id="a", job_type="chat", model="gpt-5.6-luna", effort="low", usage=Usage(input_tokens=1_000_000)
    )
    assert main(["--db", str(path), "usage", "--days", "3"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["days"] == 3
    assert summary["total_credits"] == pytest.approx(5.0)
    assert summary["budgets"] == {"task_credits": 5.0, "daily_credits": 50.0}
    assert summary["by_task"][0]["key"] == "a"
