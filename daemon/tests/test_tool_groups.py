"""Tool-group selection and lane routing.

`is_casual_conversation` picks the lane and `select_tool_group` picks the
tools, and the two are decided independently. That seam is where this fails
silently: a casual turn is dispatched with no tools, so a request that selects
a tool *and* classifies as casual has its tools thrown away before the model
sees them. Nothing errors when that happens -- a small model with no calendar,
no GitHub snapshot and no synced mail simply answers anyway, confidently and
plausibly. So the invariant is asserted directly: if a phrasing selects any
tool, it must not be casual.

Ported from the bridge, lane-routing, reminder, date, journal and nag tests
that exercised the selector, plus the eight-tool cap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from opendot_core.agent import tool_groups
from opendot_core.agent.tool_groups import (
    MAX_TOOLS_PER_GROUP,
    is_casual_conversation,
    is_fresh_mail_write,
    select_tool_group,
    wants_mail_write,
    wants_scheduling,
)

#: Phrasings a person actually types, and the tool each must reach. Written as
#: the sloppy lowercase a phone produces, apostrophes and all, because the bugs
#: above all hid behind exactly that: "replied" not matching `\breply\b`,
#: "am i free" naming no calendar word.
TOOL_BACKED_REQUESTS: list[tuple[str, str]] = [
    ("who hasnt replied to me", "threads_awaiting_reply"),
    ("what emails am i waiting on", "threads_awaiting_reply"),
    ("anyone go quiet", "threads_awaiting_reply"),
    ("who do i owe a reply", "threads_awaiting_reply"),
    ("when am i free thursday", "availability_get"),
    ("am i busy tomorrow", "availability_get"),
    ("do i have any time friday", "availability_get"),
    ("any open slots this week", "availability_get"),
    ("any prs waiting on me", "pull_requests_get"),
    ("my open pull requests", "pull_requests_get"),
    ("are all my connectors working", "connector_status"),
    ("what is on my agenda tomorrow", "agenda_get"),
    ("remind me at 3pm to call mom", "reminder_set"),
    ("log my mood as a 4", "mood_record"),
    ("when is mom's birthday", "important_dates_get"),
]

#: Ordinary conversation. These must stay casual and toolless, or every
#: throwaway message pays for a slow tool-capable turn.
CASUAL_REQUESTS = [
    "yo",
    "hey whats up",
    "how was your day",
    "no problem thanks",
    "lol",
]


@pytest.mark.parametrize("request_text,expected_tool", TOOL_BACKED_REQUESTS)
def test_a_tool_backed_question_reaches_the_work_lane(request_text: str, expected_tool: str) -> None:
    assert expected_tool in select_tool_group(request_text), request_text
    assert is_casual_conversation(request_text) is False, request_text


@pytest.mark.parametrize("request_text", CASUAL_REQUESTS)
def test_ordinary_chat_stays_free_and_toolless(request_text: str) -> None:
    assert is_casual_conversation(request_text) is True, request_text
    assert select_tool_group(request_text) == frozenset(), request_text


@pytest.mark.parametrize("request_text,_expected", TOOL_BACKED_REQUESTS)
def test_selecting_a_tool_and_routing_casual_is_never_both(request_text: str, _expected: str) -> None:
    """The invariant itself, stated once.

    A casual turn is dispatched with no tools, so choosing a tool and then
    choosing the casual lane means the tool is silently discarded. Either
    decision alone is fine; the combination is always a bug.
    """
    if select_tool_group(request_text):
        assert is_casual_conversation(request_text) is False, (
            f"{request_text!r} selects tools that the casual lane will discard"
        )


def test_tool_selection_is_bounded_and_omits_prefetched_read_tools() -> None:
    assert select_tool_group("what's going on with my inbox and github today?") == frozenset()
    assert select_tool_group("what should i work on today?") == {"agenda_get", "brief_get"}
    assert select_tool_group("draft a reply to that email") == {
        "message_draft",
    }
    assert select_tool_group("send it to mom@example.com that's my mom") == {
        "message_send_propose",
    }
    assert wants_mail_write("send an email") is True
    assert is_fresh_mail_write("send an email") is True
    assert is_fresh_mail_write("send it to mom@example.com that's my mom") is False
    assert select_tool_group("remember that I prefer short answers") == {
        "memory_search",
        "profile_get",
        "remember",
    }
    broad = select_tool_group(
        "create a calendar event, remind me, send email, file a github issue, "
        "correct memory, and show connector status"
    )
    assert len(broad) == MAX_TOOLS_PER_GROUP
    assert "action_commit" not in broad
    assert "calendar_event_propose" in broad
    assert "message_send_propose" in broad


def test_health_questions_route_to_brief_and_connector_records() -> None:
    assert select_tool_group("how did I sleep last night?") == {
        "brief_get",
        "connector_records_get",
    }
    assert select_tool_group("steps today?") == {"brief_get", "connector_records_get"}
    assert select_tool_group("how's my health") == {"brief_get", "connector_records_get"}
    assert select_tool_group("connector health") == {
        "brief_get",
        "connector_records_get",
        "connector_status",
        "system_status",
    }


def test_overflow_apps_route_to_composio_not_first_party_gmail() -> None:
    assert select_tool_group("what's on my notion?") == {"composio_search", "composio_execute"}
    assert select_tool_group("connect spotify") == {
        "composio_search",
        "composio_execute",
        "composio_connect",
    }
    assert "composio_execute" not in select_tool_group("draft a reply to that email")


def test_casual_routing_separates_chat_from_work_and_inherits_short_followups() -> None:
    assert is_casual_conversation("yo") is True
    assert is_casual_conversation("how are you today?") is True
    assert is_casual_conversation("what do you think about that movie?") is True
    assert is_casual_conversation("what should i work on today?") is False
    assert is_casual_conversation("check my calendar") is False
    assert is_casual_conversation("yeah do that", recent_topic_text="draft the email") is False


def test_wake_bedtime_and_lock_in_phrases_select_reminder_tools() -> None:
    for phrase in (
        "wake me up at 7 every day",
        "remind me at bedtime",
        "study lock-in at 8pm each night",
        "set a daily reminder to lock in",
    ):
        assert wants_scheduling(phrase)
        tools = select_tool_group(phrase)
        assert "reminder_set" in tools
        assert not is_casual_conversation(phrase)


def test_reminding_is_not_trapped_by_a_word_boundary() -> None:
    # ``\bremind\b`` never matches "reminding"; keep that class of bug closed.
    assert wants_scheduling("keep reminding me to stretch every morning")
    assert "reminder_set" in select_tool_group("keep reminding me to stretch every morning")


def test_ordinary_future_phrasing_selects_scheduling_tools() -> None:
    reminder = select_tool_group("remind me tomorrow night that im watching the odyssey")
    assert wants_scheduling("remind me tomorrow night that im watching the odyssey")
    assert "reminder_set" in reminder

    check = select_tool_group("check at 3pm who's playing")
    assert wants_scheduling("check at 3pm who's playing")
    assert "task_schedule" in check

    for phrase in ("do this at 8", "send that tomorrow night"):
        assert wants_scheduling(phrase), phrase
        tools = select_tool_group(phrase)
        assert "task_schedule" in tools or "reminder_set" in tools, phrase


def test_a_time_question_or_calendar_booking_is_not_a_scheduled_job() -> None:
    assert not wants_scheduling("what's at 3pm?")
    assert "task_schedule" not in select_tool_group("what's at 3pm?")
    assert not wants_scheduling("book a meeting at 3")
    assert "task_schedule" not in select_tool_group("book a meeting at 3")
    assert "calendar_event_propose" in select_tool_group("book a meeting at 3")


def test_birthday_phrases_select_important_date_tools() -> None:
    phrase = "remember mom's birthday is august 20 1970"
    tools = select_tool_group(phrase)
    assert "important_date_set" in tools
    assert not is_casual_conversation(phrase)


def test_mood_phrases_select_journal_tools() -> None:
    tools = select_tool_group("log my mood as a 4 today")
    assert "mood_record" in tools
    assert "journal_get" in tools
    assert not is_casual_conversation("how am i feeling today?")


def test_gratitude_phrases_select_journal_tools() -> None:
    tools = select_tool_group("gratitude journal: my family")
    assert "gratitude_record" in tools
    assert "journal_get" in tools


@pytest.mark.parametrize(
    "phrase",
    (
        "keep reminding me until I finish the essay",
        "nag me about laundry every few hours",
        "remind me until done to call mom",
        "keep reminding me to stretch every morning",
    ),
)
def test_nag_phrases_select_nag_until_done_tool(phrase: str) -> None:
    tools = select_tool_group(phrase)
    assert "nag_until_done" in tools


# One focused phrase per tool the selector can choose. Catches tools
# accidentally dropped from _TOOL_PRIORITY when a new write tool is added.
_SELECTABLE_TOOL_PHRASES: dict[str, str] = {
    "system_status": "check system status",
    "connector_status": "connector health status",
    "agenda_get": "what's on my task list",
    "brief_get": "what should i work on today",
    "memory_search": "search my memory",
    "profile_get": "what do you remember about me",
    "remember": "remember that I like tea",
    "memory_correct": "that memory is incorrect, update it",
    "memory_feedback": "that memory is incorrect, update it",
    "forget": "forget that memory",
    "calendar_event_propose": "create a calendar event for lunch",
    "message_draft": "draft a reply to that email",
    "message_send_propose": "send them an email",
    "github_issue_propose": "file a github issue about the bug",
    "connector_records_get": "what's on my calendar schedule",
    "task_upsert": "add a task to call mom",
    "task_complete": "mark the task done",
    "reminder_set": "remind me at 3pm",
    "task_schedule": "check at 3pm and text me the score",
    "important_date_set": "remember mom's birthday is august 20",
    "important_dates_get": "upcoming birthdays this week",
    "threads_awaiting_reply": "threads awaiting my reply",
    "availability_get": "when am I free on my calendar",
    "pull_requests_get": "my open pull requests",
    "mood_record": "log my mood today",
    "gratitude_record": "gratitude journal: my friends",
    "journal_get": "show my mood trend",
}


def test_every_selectable_tool_survives_the_priority_trim() -> None:
    for tool_name, phrase in _SELECTABLE_TOOL_PHRASES.items():
        selected = select_tool_group(phrase)
        assert tool_name in selected, f"{tool_name} missing for phrase: {phrase!r}"
        assert len(selected) <= MAX_TOOLS_PER_GROUP

    # action_commit is never selected at all -- not selected-then-trimmed.
    # Asserted at the selector, so removing it from _TOOL_PRIORITY alone can no
    # longer expose it.
    github_tools = select_tool_group("file a github issue about the bug")
    assert "github_issue_propose" in github_tools
    assert "action_commit" not in github_tools
    selector_body = Path(tool_groups.__file__).read_text(encoding="utf-8")
    selector_body = selector_body.split("def select_tool_group", 1)[1].split("\ndef ", 1)[0]
    assert '"action_commit"' not in selector_body, (
        "action_commit must not be selectable; the trim is not a safety boundary"
    )
    broad = select_tool_group(
        "create a calendar event, remind me, send email, file a github issue, "
        "correct memory, and show connector status"
    )
    assert "action_commit" not in broad


# --- The eight-tool cap ---------------------------------------------------


def test_the_cap_is_eight() -> None:
    assert MAX_TOOLS_PER_GROUP == 8


#: Every trigger at once: the union of the selectable phrases matches far more
#: than eight tools before the trim.
_EVERYTHING_AT_ONCE = ", ".join(_SELECTABLE_TOOL_PHRASES.values())


def test_a_request_matching_every_tool_is_trimmed_to_the_cap() -> None:
    selected = select_tool_group(_EVERYTHING_AT_ONCE)

    assert len(selected) == MAX_TOOLS_PER_GROUP


def test_the_trim_keeps_action_proposals_ahead_of_read_helpers() -> None:
    selected = select_tool_group(_EVERYTHING_AT_ONCE)

    # The highest-priority tools survive; a broad read helper does not.
    assert {"calendar_event_propose", "task_schedule", "message_draft"} <= selected
    assert "connector_records_get" not in selected
    assert "system_status" not in selected


def test_selection_is_deterministic_and_order_independent_of_input_noise() -> None:
    first = select_tool_group(_EVERYTHING_AT_ONCE)
    second = select_tool_group(_EVERYTHING_AT_ONCE)

    assert first == second
    assert isinstance(first, frozenset)


def test_no_phrase_ever_exceeds_the_cap() -> None:
    phrases = [phrase for phrase, _ in TOOL_BACKED_REQUESTS]
    phrases += list(_SELECTABLE_TOOL_PHRASES.values())
    phrases += [" and ".join(pair) for pair in zip(phrases, reversed(phrases))]

    for phrase in phrases:
        assert len(select_tool_group(phrase)) <= MAX_TOOLS_PER_GROUP, phrase


def test_the_priority_table_has_no_duplicates_and_no_commit_tool() -> None:
    priority = tool_groups._TOOL_PRIORITY

    assert len(priority) == len(set(priority))
    assert "action_commit" not in priority


def test_no_host_specific_names_remain() -> None:
    """Tool groups are host-neutral: no subprocess-agent or MCP-profile names."""
    source = Path(tool_groups.__file__).read_text(encoding="utf-8")

    banned = "|".join(("herm" + "es", "profile" + "_env", "_FILTER" + "_ENV"))
    assert re.search(banned, source, re.IGNORECASE) is None


def test_sending_mail_to_an_address_is_never_casual() -> None:
    """"send it to mom@example.com" names no work term on its own, so it used
    to be routed casually and its tool silently discarded."""
    request = "send it to mom@example.com that's my mom"

    assert is_casual_conversation(request) is False
    assert select_tool_group(request) == {"message_send_propose"}
