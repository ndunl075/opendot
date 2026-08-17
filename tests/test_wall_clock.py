from datetime import time, timedelta

import pytest

from alfred.wall_clock import format_duration, parse_hhmm


def test_parse_hhmm_reads_stored_and_configured_times() -> None:
    assert parse_hhmm("09:00") == time(9, 0)
    assert parse_hhmm("22:05") == time(22, 5)
    # Env-supplied values arrive with whitespace; stored schedules never do.
    assert parse_hhmm("  07:30 ") == time(7, 30)


def test_parse_hhmm_refuses_a_value_that_is_not_a_clock_time() -> None:
    with pytest.raises(ValueError):
        parse_hhmm("0900")
    with pytest.raises(ValueError):
        parse_hhmm("noon:00")


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0m"),
        (45, "45m"),
        (60, "1h"),
        (90, "1h 30m"),
        (450, "7h 30m"),
        (1440, "24h"),
    ],
)
def test_format_duration_says_it_the_way_a_person_would(minutes: int, expected: str) -> None:
    assert format_duration(timedelta(minutes=minutes)) == expected


def test_format_duration_drops_seconds_rather_than_rounding_up() -> None:
    # A 59-second span is not "1m": the brief and the availability report both
    # describe spans a person planned, so rounding up would overstate them.
    assert format_duration(timedelta(seconds=59)) == "0m"
    assert format_duration(timedelta(minutes=90, seconds=59)) == "1h 30m"
