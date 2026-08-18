"""A question answerable only from synced data must never reach the free lane.

`is_casual_conversation` picks the lane and `select_hermes_tools` picks the
tools, and the two are decided independently. That seam is where this fails
silently: the bridge calls `run_conversation(prompt)` for a casual turn and
passes no tools at all, so a request that selects a tool *and* classifies as
casual has its tools thrown away before the model sees them.

Nothing errors when that happens. A small model with no calendar, no GitHub
snapshot, and no synced mail simply answers anyway -- confidently, and
plausibly enough that the owner has no reason to doubt it. Four tools shipped
that way: `availability_get` had no routing at all, `threads_awaiting_reply`
was nested under a mail vocabulary that "who hasn't replied to me" does not
match, and both pull-request and connector-status questions selected a tool
and then lost it to the lane.

So the invariant is asserted directly rather than tool by tool: if a phrasing
selects any tool, it must not be casual.
"""

from __future__ import annotations

import pytest

from alfred.hermes_tools import is_casual_conversation, select_hermes_tools

#: Phrasings a person actually types, and the tool each must reach. Written
#: as the sloppy lowercase a phone produces, apostrophes and all, because the
#: bugs above all hid behind exactly that: "replied" not matching `\breply\b`,
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
    assert expected_tool in select_hermes_tools(request_text), request_text
    assert is_casual_conversation(request_text) is False, request_text


@pytest.mark.parametrize("request_text", CASUAL_REQUESTS)
def test_ordinary_chat_stays_free_and_toolless(request_text: str) -> None:
    assert is_casual_conversation(request_text) is True, request_text
    assert select_hermes_tools(request_text) == frozenset(), request_text


@pytest.mark.parametrize("request_text,_expected", TOOL_BACKED_REQUESTS)
def test_selecting_a_tool_and_routing_casual_is_never_both(request_text: str, _expected: str) -> None:
    """The invariant itself, stated once.

    A casual turn is dispatched with no tools, so choosing a tool and then
    choosing the casual lane means the tool is silently discarded. Either
    decision alone is fine; the combination is always a bug.
    """
    if select_hermes_tools(request_text):
        assert is_casual_conversation(request_text) is False, (
            f"{request_text!r} selects tools that the casual lane will discard"
        )
