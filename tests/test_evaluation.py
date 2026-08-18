from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.db import Database
from alfred.evaluation import EvaluationService
from alfred.response_feedback import ResponseFeedbackService


def _feedback(
    database: Database,
    *,
    response_id: str,
    outcome: str,
    sources: list[str],
    index: int,
    signal: str = "button",
    rule: str | None = None,
) -> None:
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id=response_id,
                sources=sources,
                freshness={source: None for source in sources},
                items=[],
            )
            ResponseFeedbackService.record_feedback_in_transaction(
                connection,
                callback_query_id=f"callback-{index}" if signal == "button" else None,
                feedback_update_id=str(900 + index),
                response_update_id=response_id,
                outcome=outcome,
                signal=signal,
                rule=rule,
            )


def test_empty_database_reports_zero_without_inventing_a_rate(tmp_path: Path) -> None:
    report = EvaluationService(Database(tmp_path / "alfred.db")).report()

    assert report.response_feedback.total == 0
    # A rate over zero votes is undefined, not 0.0 -- reporting 0.0 would read
    # as "everything failed" instead of "nothing was measured yet".
    assert report.response_feedback.positive_rate is None
    assert report.retrieval_feedback.positive_rate is None
    assert report.workflows.acceptance_rate is None
    assert report.memory_learning.promotion_rate is None
    assert report.sources == []


def test_every_known_outcome_is_reported_even_when_never_chosen(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="10", outcome="helpful", sources=["gmail"], index=0)

    report = EvaluationService(database).report()

    assert report.response_feedback.counts == {
        "helpful": 1,
        "missing_context": 0,
        "wrong_context": 0,
    }
    assert report.response_feedback.positive_rate == 1.0


def test_the_report_says_how_each_verdict_was_reached(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="15", outcome="helpful", sources=["gmail"], index=0)
    _feedback(
        database,
        response_id="16",
        outcome="wrong_context",
        sources=["gmail"],
        index=1,
        signal="reply",
    )

    report = EvaluationService(database).report()

    assert report.response_feedback.signals == {"button": 1, "reply": 1}
    assert report.response_feedback.total == 2


def test_a_quiet_connector_does_not_read_as_answers_the_owner_disliked(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="17", outcome="helpful", sources=["gmail"], index=0, signal="reply")
    for index, response_id in enumerate(["18", "19"], start=1):
        _feedback(
            database,
            response_id=response_id,
            outcome="missing_context",
            sources=["gmail"],
            index=index,
            signal="coverage",
            rule="stale:gmail",
        )

    report = EvaluationService(database).report()

    # Alfred flagging its own stale pack is connector health, not a verdict on
    # the answer, so it is counted apart and cannot drag the helpful rate down.
    assert report.response_feedback.total == 1
    assert report.response_feedback.positive_rate == 1.0
    assert report.context_gaps.total == 2
    assert report.context_gaps.by_source == {"gmail": 2}
    assert [source.missing_context for source in report.sources] == [0]


def test_positive_rate_counts_only_helpful_votes(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    for index, outcome in enumerate(["helpful", "helpful", "wrong_context", "missing_context"]):
        _feedback(
            database,
            response_id=str(20 + index),
            outcome=outcome,
            sources=["gmail"],
            index=index,
        )

    report = EvaluationService(database).report()

    assert report.response_feedback.total == 4
    assert report.response_feedback.positive_rate == 0.5


def test_source_quality_joins_each_vote_to_that_turns_sources(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="30", outcome="helpful", sources=["gmail", "memory"], index=0)
    _feedback(database, response_id="31", outcome="wrong_context", sources=["gmail"], index=1)
    _feedback(database, response_id="32", outcome="helpful", sources=["gmail"], index=2)

    report = EvaluationService(database).report()
    by_source = {item.source: item for item in report.sources}

    # gmail was present for all three votes, memory for only the one.
    assert by_source["gmail"].responses == 3
    assert by_source["gmail"].helpful == 2
    assert by_source["gmail"].wrong_context == 1
    assert by_source["memory"].responses == 1
    assert by_source["memory"].helpful == 1
    # Most-voted-on source first, so the actionable signal leads.
    assert report.sources[0].source == "gmail"


def test_feedback_outside_the_window_is_excluded(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="40", outcome="helpful", sources=["gmail"], index=0)
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute("UPDATE response_feedback SET created_at = ?", (old,))

    assert EvaluationService(database).report(window_days=30).response_feedback.total == 0
    assert EvaluationService(database).report(window_days=90).response_feedback.total == 1


def test_workflow_acceptance_rate_ignores_still_pending_proposals(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        with database.transaction(connection):
            for index, state in enumerate(["accepted", "rejected", "pending", "pending"]):
                connection.execute(
                    """
                    INSERT INTO workflow_skill_versions (
                        id, pattern_signature, skill_name, version, state,
                        definition_json, skill_markdown, diff_text, content_hash,
                        occurrence_count, distinct_days, first_observed_at,
                        last_observed_at, created_at
                    ) VALUES (?, ?, ?, 1, ?, '{}', '', '', ?, 3, 2, ?, ?, ?)
                    """,
                    (
                        f"version-{index}",
                        f"signature-{index}",
                        f"learned-skill-{index}",
                        state,
                        f"hash-{index}",
                        now,
                        now,
                        now,
                    ),
                )

    workflows = EvaluationService(database).report().workflows

    assert workflows.proposed == 4
    assert workflows.pending == 2
    # One accepted of two decided -- the two pending must not drag this down.
    assert workflows.acceptance_rate == 0.5


def test_report_selects_no_prompt_answer_or_statement_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _feedback(database, response_id="50", outcome="helpful", sources=["gmail"], index=0)

    serialized = EvaluationService(database).report().model_dump_json()

    # The report is meant to be safe to paste into an issue; it should carry
    # aggregate shape only, never anything a person wrote or received.
    assert "query" not in serialized
    assert "statement" not in serialized
    assert "callback" not in serialized
