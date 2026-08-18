from pathlib import Path

from alfred.db import Database
from alfred.implicit_feedback import SIGNAL_COVERAGE, SIGNAL_REPLY
from alfred.response_feedback import ResponseFeedbackService


def _record(
    database: Database,
    *,
    response_id: str,
    outcome: str,
    signal: str = "button",
    index: int = 0,
) -> None:
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id=response_id,
                sources=["gmail"],
                freshness={"gmail": None},
                items=[{"source": "gmail", "record_id": "message-1", "rank": 0}],
            )
            ResponseFeedbackService.record_feedback_in_transaction(
                connection,
                response_update_id=response_id,
                outcome=outcome,
                signal=signal,
                callback_query_id=f"callback-{index}" if signal == "button" else None,
                feedback_update_id=str(200 + index),
            )


def test_feedback_scores_are_bounded_and_missing_context_does_not_guess(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    for index, outcome in enumerate(
        ["helpful", "helpful", "helpful", "wrong_context", "missing_context"]
    ):
        _record(database, response_id=str(100 + index), outcome=outcome, index=index)

    scores = ResponseFeedbackService(database).scores(
        source="gmail", record_ids={"message-1", "message-2"}
    )

    assert scores == {"message-1": 2}


def test_an_inferred_verdict_moves_ranking_the_same_way_a_tap_did(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _record(database, response_id="150", outcome="wrong_context", signal=SIGNAL_REPLY)

    scores = ResponseFeedbackService(database).scores(
        source="gmail", record_ids={"message-1"}
    )

    assert scores == {"message-1": -1}


def test_one_response_judged_twice_still_counts_once_and_the_owner_wins(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    # Alfred flagged its own stale context, then the owner said it was fine.
    _record(database, response_id="160", outcome="missing_context", signal=SIGNAL_COVERAGE)
    _record(database, response_id="160", outcome="helpful", signal=SIGNAL_REPLY, index=1)

    scores = ResponseFeedbackService(database).scores(
        source="gmail", record_ids={"message-1"}
    )

    with database.connect() as connection:
        stored = connection.execute(
            "SELECT signal, outcome FROM response_feedback WHERE response_update_id = '160' "
            "ORDER BY signal"
        ).fetchall()

    assert [(row["signal"], row["outcome"]) for row in stored] == [
        ("coverage", "missing_context"),
        ("reply", "helpful"),
    ]
    assert scores == {"message-1": 1}


def test_a_detector_cannot_vote_twice_on_the_same_response(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _record(database, response_id="170", outcome="wrong_context", signal=SIGNAL_REPLY)

    with database.connect() as connection:
        with database.transaction(connection):
            second = ResponseFeedbackService.record_feedback_in_transaction(
                connection,
                response_update_id="170",
                outcome="helpful",
                signal=SIGNAL_REPLY,
                feedback_update_id="999",
            )

    assert second.recorded is False


def test_coverage_signal_records_a_gap_the_owner_could_not_have_seen(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id="180",
                sources=["gmail"],
                freshness={"gmail": None},
                items=[],
            )
            receipt = ResponseFeedbackService.record_coverage_signal_in_transaction(
                connection,
                response_update_id="180",
                sources=["gmail"],
                freshness={"gmail": None},
            )

    assert receipt is not None
    assert (receipt.outcome, receipt.signal, receipt.recorded) == (
        "missing_context",
        SIGNAL_COVERAGE,
        True,
    )
