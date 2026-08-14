from pathlib import Path

from alfred.db import Database
from alfred.response_feedback import ResponseFeedbackService, feedback_keyboard


def test_feedback_scores_are_bounded_and_missing_context_does_not_guess(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            for index, outcome in enumerate(
                ["helpful", "helpful", "helpful", "wrong_context", "missing_context"]
            ):
                response_id = str(100 + index)
                ResponseFeedbackService.record_context_in_transaction(
                    connection,
                    response_update_id=response_id,
                    sources=["gmail"],
                    freshness={"gmail": None},
                    items=[{"source": "gmail", "record_id": "message-1", "rank": 0}],
                )
                ResponseFeedbackService.record_feedback_in_transaction(
                    connection,
                    callback_query_id=f"callback-{index}",
                    feedback_update_id=str(200 + index),
                    response_update_id=response_id,
                    outcome=outcome,
                )

    scores = ResponseFeedbackService(database).scores(
        source="gmail", record_ids={"message-1", "message-2"}
    )

    assert scores == {"message-1": 2}


def test_feedback_keyboard_has_short_non_authorizing_callback_data() -> None:
    keyboard = feedback_keyboard("251171850")
    buttons = [button for row in keyboard["inline_keyboard"] for button in row]

    assert [button["text"] for button in buttons] == [
        "helpful",
        "missing context",
        "wrong context",
    ]
    assert all(len(button["callback_data"].encode("utf-8")) <= 64 for button in buttons)
    assert all("approve" not in button["callback_data"] for button in buttons)
