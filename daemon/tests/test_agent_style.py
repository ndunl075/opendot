"""Reply style: dash/markdown rules, bubble splitting, tool-narration stripping.

The first block is ported from the bridge tests; the narration block is the
former ``test_tool_narration.py``.
"""

from __future__ import annotations

from opendot_core.agent.style import (
    MAX_BUBBLE_CHARS,
    enforce_style,
    split_into_bubbles,
)


def test_split_keeps_a_single_paragraph_as_one_bubble() -> None:
    assert split_into_bubbles("just the one thing.") == ["just the one thing."]


def test_an_answer_is_split_into_one_bubble_per_paragraph() -> None:
    """Each paragraph becomes its own message so a reply reads like someone
    texting, not a wall."""
    bubbles = split_into_bubbles("3 tasks due today.\n\nnone overdue.\n\nwant the list?")

    assert bubbles == ["3 tasks due today.", "none overdue.", "want the list?"]


def test_a_reply_longer_than_the_channel_limit_is_truncated() -> None:
    (bubble,) = split_into_bubbles("x" * (MAX_BUBBLE_CHARS * 2))

    assert len(bubble) <= MAX_BUBBLE_CHARS
    assert bubble.endswith("[truncated]")


def test_em_dashes_become_sentence_breaks() -> None:
    """The persona forbids dashes and the model used one in its first live
    reply anyway, so this rule is enforced in code rather than only asked for."""
    assert enforce_style("not much on my end — just here.") == "not much on my end. just here."
    assert enforce_style("3 tasks–2 overdue") == "3 tasks. 2 overdue"


def test_markdown_emphasis_is_stripped() -> None:
    """Replies are plain text, so '**inbox**' arrived on the phone as literal
    asterisks. The persona forbids markdown; this is the backstop."""
    assert enforce_style("**inbox**. 10 unread") == "inbox. 10 unread"
    assert enforce_style("the *vendor* one matters") == "the vendor one matters"
    assert enforce_style("__bold__ and ___both___") == "bold and both"


def test_markdown_headings_are_stripped() -> None:
    assert enforce_style("## inbox\n10 unread") == "inbox\n10 unread"


def test_bare_asterisks_are_not_mistaken_for_emphasis() -> None:
    """Only paired emphasis is stripped, so a stray asterisk survives."""
    assert enforce_style("2 * 3 = 6") == "2 * 3 = 6"


def test_plain_hyphens_are_left_alone() -> None:
    """Hyphens are real punctuation inside words and in the short "- item"
    lists the persona allows; only clause-joining em/en dashes are rewritten."""
    assert enforce_style("re-run the fine-grained sync") == "re-run the fine-grained sync"
    assert enforce_style("- file taxes\n- call mom") == "- file taxes\n- call mom"


def test_a_dash_after_sentence_punctuation_does_not_double_the_period() -> None:
    assert enforce_style("done. — next up") == "done. next up"


def test_bubbles_are_style_enforced_too() -> None:
    assert split_into_bubbles("first — thing\n\nsecond one") == ["first. thing", "second one"]


def test_split_folds_extra_paragraphs_into_the_last_bubble() -> None:
    """Nothing is dropped when the model overruns the bubble budget."""
    text = "\n\n".join(["one", "two", "three", "four", "five", "six"])

    bubbles = split_into_bubbles(text, max_bubbles=3)

    assert len(bubbles) == 3
    assert bubbles[:2] == ["one", "two"]
    assert bubbles[2] == "three\n\nfour\n\nfive\n\nsix"


def test_split_never_returns_an_empty_list_for_blank_output() -> None:
    assert split_into_bubbles("   \n\n  \n") == ["(no answer)"]


def test_each_bubble_is_individually_truncated() -> None:
    text = ("a" * (MAX_BUBBLE_CHARS * 2)) + "\n\n" + "short"

    bubbles = split_into_bubbles(text)

    assert len(bubbles) == 2
    assert len(bubbles[0]) <= MAX_BUBBLE_CHARS
    assert bubbles[1] == "short"


# --- Tool narration -------------------------------------------------------
#
# Observed live: asked "when am i free thursday", the agent replied in three
# chat bubbles, the middle one being "it looks like mcp__alfred__availability_get
# is what i need." The tool was offered and the model narrated choosing it
# instead of calling it. Prompting addresses the cause; this is the guarantee.

LIVE_REPLY = (
    "i need to check your calendar for that. let me just pull up the tool.\n\n"
    "it looks like mcp__alfred__availability_get is what i need.\n\n"
    "i'll use that to find your free slots for thursday."
)


def test_the_live_leak_no_longer_reaches_the_owner() -> None:
    assert "mcp__alfred__availability_get" not in enforce_style(LIVE_REPLY)
    assert "mcpalfredavailability_get" not in enforce_style(LIVE_REPLY)


def test_the_opendot_namespace_is_caught_too() -> None:
    cleaned = enforce_style("it looks like mcp__opendot__availability_get is what i need.")

    assert "mcp__opendot" not in cleaned
    assert "mcpopendot" not in cleaned


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
