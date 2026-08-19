"""The send-an-email turn, from a live failure the owner hit on their phone.

Asked to "send an email to my mom (mom@example.com) and tell her who you
are", Alfred replied "what's your mom's email? the address was redacted from
what i see here". The owner sent the address on its own. Alfred answered
"checking your inbox...". No email was ever drafted.

Two independent bugs met in one turn:

*The recipient was scrubbed before the model saw it.* Redaction ran over the
whole prompt, so the address the owner had just typed became
``[REDACTED:email]``. Retyping it could not help -- the second message was
scrubbed the same way -- so the loop had no exit.

*A bare address read as an inbox request.* An email address carries its own
provider name, so "gmail" inside ``mom@example.com`` matched the inbox
keyword. The send path already guarded that for "send it to x@y.com"; a bare
address names no verb, so nothing guarded it.
"""

from __future__ import annotations

from alfred.db import Database
from alfred.hermes_bridge import _redact_except_current_request
from alfred.models import Redactor
from alfred.telegram import TelegramGateway

ADDRESS = "mom@example.com"


def _gateway() -> TelegramGateway:
    return TelegramGateway(Database(":memory:"), set())


def _prompt(request: str, context: str = "synced snippet from bob@work.example here") -> str:
    return f"alfred runtime context follows. {context}\ncurrent request: {request}"


def test_the_recipient_the_owner_typed_survives() -> None:
    out = _redact_except_current_request(
        _prompt(f"send an email to my mom ({ADDRESS}) and tell her who you are"), Redactor()
    )

    assert ADDRESS in out


def test_a_bare_address_survives_too() -> None:
    """The second half of the loop: answering Alfred's own question with just
    the address."""
    assert ADDRESS in _redact_except_current_request(_prompt(ADDRESS), Redactor())


def test_synced_content_is_still_scrubbed() -> None:
    """The point of redaction is unchanged: Alfred's stored content about
    other people must not reach a cloud model as a side effect."""
    out = _redact_except_current_request(_prompt("what's up"), Redactor())

    assert "bob@work.example" not in out
    assert "REDACTED" in out


def test_an_unexpected_prompt_shape_fails_closed() -> None:
    """No marker means the whole thing is treated as context, not as the
    owner's words -- erring toward scrubbing rather than leaking."""
    out = _redact_except_current_request("no marker here, bob@work.example", Redactor())

    assert "bob@work.example" not in out


def test_a_bare_address_is_not_an_inbox_request() -> None:
    assert _gateway().acknowledgement_for(ADDRESS) == ""


def test_naming_a_recipient_still_reads_as_a_draft() -> None:
    ack = _gateway().acknowledgement_for(f"send an email to my mom ({ADDRESS})")

    assert ack == f"drafting email to {ADDRESS}..."


def test_a_real_inbox_question_still_says_inbox() -> None:
    """The fix must not cost the ordinary case."""
    for question in ("whats in my inbox", "check my gmail", "any unread mail"):
        assert _gateway().acknowledgement_for(question) == "checking your inbox...", question


def test_each_action_gets_its_own_acknowledgement() -> None:
    """One "on it..." for every write told the owner nothing about which
    promise Alfred had just made."""
    gateway = _gateway()
    expected = {
        "keep reminding me to file the fafsa": "i'll keep on you about it...",
        "remind me at 3pm": "setting that reminder...",
        "log my mood as a 4": "logging that...",
        "grateful for the walk home": "writing that down...",
        "mark that done": "marking that done...",
        "forget that memory": "forgetting that...",
    }
    for request, ack in expected.items():
        assert gateway.acknowledgement_for(request) == ack, request


def test_a_question_names_what_is_being_read() -> None:
    gateway = _gateway()
    expected = {
        "who hasnt replied to me": "checking who's waiting on you...",
        "any prs waiting on me": "checking your open pull requests...",
        "when am i free thursday": "checking when you're free...",
        "when is moms birthday": "checking upcoming dates...",
    }
    for request, ack in expected.items():
        assert gateway.acknowledgement_for(request) == ack, request


def test_a_pull_request_question_does_not_also_name_the_connector() -> None:
    """Matching both left "checking your open pull requests and github...",
    naming the connector as though it were a second topic."""
    assert _gateway().acknowledgement_for("any prs waiting on me") == (
        "checking your open pull requests..."
    )


def test_ordinary_chat_still_gets_no_acknowledgement() -> None:
    for chatter in ("yo", "hey whats up", "lol"):
        assert _gateway().acknowledgement_for(chatter) == "", chatter
