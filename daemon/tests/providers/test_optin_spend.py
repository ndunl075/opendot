from datetime import UTC, datetime

import pytest

from opendot_core.db import Database
from opendot_core.providers import SpendCapReached, SpendTracker
from opendot_core.providers.spend import PRICE_TABLE, UNKNOWN_RATE, rate_for
from opendot_core.providers.types import Usage


def _tracker(tmp_path):
    clock = {"now": datetime(2026, 10, 5, tzinfo=UTC)}
    return SpendTracker(Database(tmp_path / "s.db"), now=lambda: clock["now"]), clock


def test_default_cap_is_zero_and_fails_closed(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    assert tracker.get_cap("openai_key") == 0.0
    with pytest.raises(SpendCapReached):
        tracker.check("openai_key", model="gpt-4o")


def test_cap_and_spend_persist_across_instances(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    tracker.set_cap("openai_key", 2.0)
    tracker.record("openai_key", "gpt-4o", Usage(input_tokens=1000, output_tokens=1000))
    again, _ = _tracker(tmp_path)
    assert again.get_cap("openai_key") == 2.0
    assert again.spent("openai_key") == pytest.approx(0.0125)
    again.check("openai_key", model="gpt-4o")


def test_caps_are_per_provider(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    tracker.set_cap("openai_key", 5.0)
    with pytest.raises(SpendCapReached):
        tracker.check("anthropic_key", model="claude-sonnet-5")


def test_reaching_the_cap_blocks(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    tracker.set_cap("openai_key", 0.01)
    tracker.record("openai_key", "gpt-4o", Usage(input_tokens=1000, output_tokens=1000))
    with pytest.raises(SpendCapReached):
        tracker.check("openai_key", model="gpt-4o")


def test_large_estimated_input_is_blocked_before_it_is_sent(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    tracker.set_cap("openai_key", 0.01)
    with pytest.raises(SpendCapReached):
        tracker.check("openai_key", model="gpt-4o", estimated_input_tokens=10_000)


def test_spend_resets_each_month(tmp_path) -> None:
    tracker, clock = _tracker(tmp_path)
    tracker.set_cap("openai_key", 0.01)
    tracker.record("openai_key", "gpt-4o", Usage(input_tokens=1000, output_tokens=1000))
    clock["now"] = datetime(2026, 11, 1, tzinfo=UTC)
    assert tracker.spent("openai_key") == 0.0
    tracker.check("openai_key", model="gpt-4o")


def test_unknown_rate_is_conservative(tmp_path) -> None:
    rate, known = rate_for("openai_key", "brand-new-model")
    assert known is False and rate == UNKNOWN_RATE
    for table in PRICE_TABLE.values():
        for known_rate in table.values():
            assert UNKNOWN_RATE[0] >= known_rate[0] and UNKNOWN_RATE[1] >= known_rate[1]
    tracker, _ = _tracker(tmp_path)
    cost = tracker.record("openai_key", "brand-new-model", Usage(input_tokens=1000, output_tokens=1000))
    assert cost == pytest.approx(0.225)


def test_missing_usage_falls_back_to_the_input_estimate(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    cost = tracker.record("openai_key", "gpt-4o", None, fallback_input_tokens=2000)
    assert cost == pytest.approx(0.005)


def test_negative_cap_rejected(tmp_path) -> None:
    tracker, _ = _tracker(tmp_path)
    with pytest.raises(ValueError):
        tracker.set_cap("openai_key", -1)
