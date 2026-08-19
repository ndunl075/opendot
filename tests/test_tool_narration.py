"""An internal tool name must never reach the owner's phone.

Observed live, not imagined. Asked "when am i free thursday", the agent
replied in three chat bubbles:

    i need to check your calendar for that. let me just pull up the tool.
    it looks like mcp__alfred__availability_get is what i need.
    i'll use that to find your free slots for thursday.

The tool was offered and the model narrated choosing it instead of calling
it, so the owner got three messages of the agent thinking out loud and no
answer. SOUL.md already forbids surfacing runtime internals -- job ids, cron
expressions, gateways -- and a namespaced MCP identifier is the same class.

Prompting addresses the cause. This is the guarantee: a prompt is a request,
and the model had already ignored the "go check" instruction it was given.
"""

from __future__ import annotations

from alfred.hermes_bridge import enforce_style, split_into_bubbles

LIVE_REPLY = (
    "i need to check your calendar for that. let me just pull up the tool.\n\n"
    "it looks like mcp__alfred__availability_get is what i need.\n\n"
    "i'll use that to find your free slots for thursday."
)


def test_the_live_leak_no_longer_reaches_the_owner() -> None:
    assert "mcp__alfred__availability_get" not in enforce_style(LIVE_REPLY)
    assert "mcpalfredavailability_get" not in enforce_style(LIVE_REPLY)


def test_the_underscore_eaten_spelling_is_caught_too() -> None:
    """Markdown emphasis eats the `__` pairs, so the identifier arrives as
    `mcpalfredavailability_get` -- which is exactly the form that was
    delivered. Matching only the raw spelling would have missed the real
    bug."""
    assert "mcpalfred" not in enforce_style("it looks like mcpalfredavailability_get works.")


def test_a_whole_narrating_sentence_goes_not_just_the_name() -> None:
    """Deleting the identifier alone leaves "it looks like is what i need"."""
    cleaned = enforce_style(
        "your thursday is open after 2pm.\n\nit looks like mcp__alfred__availability_get is what i need."
    )
    assert "your thursday is open after 2pm." in cleaned
    assert "is what i need" not in cleaned


def test_an_ordinary_answer_is_untouched() -> None:
    for reply in (
        "your thursday is open after 2pm.",
        "you have 3 unread from robin.",
        "nothing on the calendar tomorrow.",
    ):
        assert enforce_style(reply) == reply


def test_a_reply_that_is_only_narration_still_says_something() -> None:
    """If every sentence names a tool there is nothing worth keeping, so the
    identifiers are dropped instead. An odd sentence beats an empty reply --
    an empty one would be delivered as a blank message."""
    cleaned = enforce_style("mcp__alfred__availability_get is what i need.")

    assert "mcp" not in cleaned
    assert cleaned.strip() != ""


def test_the_scrub_survives_bubble_splitting() -> None:
    """Bubbles are the delivery unit, and the leak arrived as its own bubble,
    so the guarantee has to hold after splitting rather than before."""
    bubbles = split_into_bubbles(LIVE_REPLY)

    assert bubbles, "a reply must never be emptied entirely"
    for bubble in bubbles:
        assert "mcp" not in bubble.lower()


def test_a_word_merely_containing_mcp_is_not_mangled() -> None:
    """The pattern is anchored to the namespaced form, not to the letters."""
    for safe in ("the mcp server is running.", "check mcp_server logs."):
        assert enforce_style(safe) == safe
