"""Alfred writing as the owner, rather than as "[name]".

Asked to introduce itself to the owner's mother, Alfred proposed a letter
beginning "I'm Alfred, a personal assistant that helps [name] manage emails".
The placeholder survived all the way to the approval preview.

The model was not careless. Nothing in the prompt named the owner -- the
`self` entity is labelled "Alfred owner" with no properties -- so a letter
that needed a name had no name to use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alfred.db import Database
from alfred.events import EventStore
from alfred.owner_identity import owner_identity, unfilled_placeholders


def _sent(database: Database, sender: str, external_id: str) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            EventStore.append(
                connection,
                source="gmail",
                external_id=external_id,
                occurred_at=datetime.now(UTC),
                content="a sent message",
                metadata={"from": sender, "outbound": True},
            )


def test_the_name_comes_from_the_owners_own_sent_mail(tmp_path: Path) -> None:
    """Their own From header is how they already sign email, so it is a
    choice rather than an inference from an address."""
    database = Database(tmp_path / "alfred.db")
    for index in range(3):
        _sent(database, "Alex Owner <owner@example.com>", f"s{index}")

    who = owner_identity(database)

    assert who.name == "Alex Owner"
    assert who.address == "owner@example.com"


def test_the_most_used_header_wins_over_a_one_off_alias(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    for index in range(4):
        _sent(database, "Alex Owner <owner@example.com>", f"main{index}")
    _sent(database, "Alex Owner <owner@work.example>", "alias")

    assert owner_identity(database).address == "owner@example.com"


def test_a_header_with_no_real_name_is_ignored(tmp_path: Path) -> None:
    """Signing "owner@example.com," is worse than signing nothing."""
    database = Database(tmp_path / "alfred.db")
    _sent(database, "owner@example.com <owner@example.com>", "bare")

    assert owner_identity(database).name is None


def test_no_sent_mail_means_no_claim(tmp_path: Path) -> None:
    """A prompt with no name is recoverable; one with a guessed name signs
    letters as somebody else."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    who = owner_identity(database)

    assert who.name is None
    assert who.prompt_line() == ""


def test_the_prompt_line_says_who_to_write_as(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _sent(database, "Alex Owner <owner@example.com>", "s1")

    line = owner_identity(database).prompt_line()

    assert "Alex Owner" in line
    assert "sign mail as them" in line
    assert "[name]" in line, "the instruction names the exact failure it prevents"


def test_the_live_placeholder_is_caught() -> None:
    body = "Hi,\n\nI'm Alfred, a personal assistant that helps [name] manage emails."

    assert unfilled_placeholders(body) == ["[name]"]


def test_the_common_placeholder_shapes_are_caught() -> None:
    for body in (
        "regards, [your name]",
        "call me on [phone]",
        "[insert date here] works",
        "from [Your Name]",
        "reach me at [email]",
    ):
        assert unfilled_placeholders(body), body


def test_ordinary_bracketed_prose_is_left_alone() -> None:
    """A guard that fires on real writing would block legitimate letters."""
    for body in (
        "he wrote [sic] in the margin",
        "see [1] for the source",
        "the meeting [which ran long] finished at six",
        "no brackets at all here",
    ):
        assert unfilled_placeholders(body) == [], body


def test_a_finished_letter_passes() -> None:
    body = "Hi Mom,\n\nI'm Alfred, the assistant Alex built. He asked me to say hello.\n\nAlex"

    assert unfilled_placeholders(body) == []
