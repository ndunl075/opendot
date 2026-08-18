from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.implicit_feedback import classify_reply, detect_context_gap
from alfred.response_feedback import ResponseFeedbackService
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


def _message(
    update_id: int,
    text: str,
    *,
    chat_id: int = 20,
    user_id: int = 10,
    at: datetime | None = None,
) -> TelegramUpdate:
    moment = at or datetime.now(UTC)
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "date": int(moment.timestamp()),
                "chat": {"id": chat_id},
                "from": {"id": user_id},
                "text": text,
            },
        }
    )


def _gateway(database: Database) -> TelegramGateway:
    return TelegramGateway(
        database,
        {TelegramPair(chat_id=20, user_id=10)},
        defer_unparsed_to_agent=True,
    )


def _answered(database: Database, update_id: int, text: str = "what's in my inbox?") -> None:
    """Ask a question and record the trace the bridge would have stored."""
    _gateway(database).handle(_message(update_id, text))
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id=str(update_id),
                sources=["gmail"],
                freshness={"gmail": datetime.now(UTC).isoformat()},
                items=[{"source": "gmail", "record_id": "message-1", "rank": 0}],
            )


def _verdicts(database: Database) -> list[tuple[str, str, str, str]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT response_update_id, signal, outcome, rule FROM response_feedback "
            "ORDER BY created_at, rowid"
        ).fetchall()
    return [
        (
            str(row["response_update_id"]),
            str(row["signal"]),
            str(row["outcome"]),
            str(row["rule"]),
        )
        for row in rows
    ]


@pytest.mark.parametrize(
    ("message", "outcome"),
    [
        ("that's wrong, the meeting is on tuesday", "wrong_context"),
        ("no it isn't, i moved that last week", "wrong_context"),
        ("you sent the wrong thread", "wrong_context"),
        ("that's not what i asked", "wrong_context"),
        ("i never said that", "wrong_context"),
        ("i don't think that's right", "wrong_context"),
        ("thanks but you missed the one from sam", "missing_context"),
        ("you didn't mention the canvas assignment", "missing_context"),
        ("that's not all of them", "missing_context"),
        ("did you check github too", "missing_context"),
        ("there's also the invoice from stripe", "missing_context"),
        ("thanks, that's perfect", "helpful"),
        ("ty", "helpful"),
        ("exactly what i needed", "helpful"),
        ("that helps a lot", "helpful"),
        ("nice", "helpful"),
        ("great, now draft a reply", "helpful"),
    ],
)
def test_the_next_message_carries_the_verdict_the_buttons_used_to_collect(
    message: str, outcome: str
) -> None:
    verdict = classify_reply(message)

    assert verdict is not None
    assert verdict.outcome == outcome


@pytest.mark.parametrize(
    "message",
    [
        "what's on my calendar tomorrow",
        # Declining an offer is not praise, even though it says "thanks".
        "no thanks",
        "nah i'm good",
        # Alfred's replies end with offers, so a bare "no" answers a question
        # rather than disputing a fact.
        "no",
        "yeah do that",
        # A question containing "wrong" is not a complaint about the answer.
        "what's wrong with the ci build",
        "what about tomorrow",
        # A praise word describing something other than the answer.
        "beautiful day today",
        "what exactly did she say",
        "great question for the exam",
        # Thanking for the next thing, not the last one.
        "can you check my inbox, thanks in advance",
        "",
    ],
)
def test_an_ordinary_message_records_nothing_rather_than_guessing(message: str) -> None:
    assert classify_reply(message) is None


def test_a_correction_scores_the_last_answer_without_being_asked(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _answered(database, 40)

    receipt = _gateway(database).handle(_message(41, "you missed the one from sam"))

    assert _verdicts(database) == [("40", "reply", "missing_context", "omitted")]
    assert receipt.feedback_recorded is True
    # The owner is never told a vote was taken; the ack is the ordinary one.
    assert "feedback" not in receipt.text
    assert receipt.agent_deferred is True


def test_the_newest_answered_turn_is_the_one_being_reacted_to(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _answered(database, 50, "what's in my inbox?")
    _answered(database, 51, "what's on my calendar?")

    _gateway(database).handle(_message(52, "that's wrong, i moved it"))

    assert _verdicts(database) == [("51", "reply", "wrong_context", "denial")]


def test_a_reaction_arriving_a_day_later_is_not_scored(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _answered(database, 60)
    stale = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute("UPDATE response_context SET created_at = ?", (stale,))

    _gateway(database).handle(_message(61, "that's wrong"))

    assert _verdicts(database) == []


def test_only_the_sender_who_asked_can_score_the_answer(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _answered(database, 70)
    gateway = TelegramGateway(
        database,
        {TelegramPair(chat_id=20, user_id=10), TelegramPair(chat_id=21, user_id=11)},
        defer_unparsed_to_agent=True,
    )

    gateway.handle(_message(71, "that's wrong", chat_id=21, user_id=11))

    assert _verdicts(database) == []


def test_a_second_correction_does_not_pile_onto_the_same_answer(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _answered(database, 80)
    gateway = _gateway(database)

    gateway.handle(_message(81, "that's wrong"))
    gateway.handle(_message(82, "you forgot the other one too"))

    assert _verdicts(database) == [("80", "reply", "wrong_context", "denial")]


def test_stale_context_is_flagged_by_alfred_rather_than_by_the_owner() -> None:
    now = datetime.now(UTC)
    fresh = (now - timedelta(minutes=5)).isoformat()
    old = (now - timedelta(days=3)).isoformat()

    assert (
        detect_context_gap(sources=["gmail"], freshness={"gmail": fresh}, now=now) is None
    )
    stale = detect_context_gap(sources=["gmail"], freshness={"gmail": old}, now=now)
    unsynced = detect_context_gap(
        sources=["github"], freshness={"github": None}, now=now
    )

    assert stale is not None and stale.rule == "stale:gmail"
    assert unsynced is not None and unsynced.outcome == "missing_context"


def test_the_bridge_flags_its_own_stale_pack_while_answering(tmp_path: Path) -> None:
    from alfred.connector_records import ConnectorRecordStore
    from alfred.hermes_bridge import AgentRunResult, HermesBridge

    database = Database(tmp_path / "alfred.db")
    database.migrate()
    long_ago = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                records={"m1": {"subject": "rent is due", "from": "landlord@example.com"}},
            )
            connection.execute(
                """
                INSERT INTO sync_state (connector, account, last_success_at, updated_at)
                VALUES ('gmail', 'self', ?, ?)
                """,
                (long_ago, long_ago),
            )
    _gateway(database).handle(_message(90, "anything important in my inbox?"))

    HermesBridge(database, lambda prompt: AgentRunResult(text="one email matters.", ok=True)).run_once()

    assert _verdicts(database) == [("90", "coverage", "missing_context", "stale:gmail")]


def test_a_source_with_no_freshness_to_check_is_not_invented() -> None:
    # Memory and recent conversation are packed from local tables that have no
    # sync at all, so they can never be "stale" the way a connector can.
    assert (
        detect_context_gap(
            sources=["memory", "recent_conversation"], freshness={}, now=datetime.now(UTC)
        )
        is None
    )
