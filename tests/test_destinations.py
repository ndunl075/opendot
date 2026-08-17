import pytest

from alfred.destinations import destination_from_payload, resolve_destination


def test_chat_id_remains_a_telegram_compatibility_bridge() -> None:
    assert resolve_destination(None, 20, noun="reminder") == "telegram:20"
    assert resolve_destination(None, -100123, noun="reminder") == "telegram:-100123"


def test_an_explicit_destination_is_passed_through_unchanged() -> None:
    assert resolve_destination("slack:D123", None, noun="nag") == "slack:D123"
    # An explicit destination wins; the chat_id bridge is not consulted.
    assert resolve_destination("slack:D123", 20, noun="nag") == "slack:D123"


def test_refusals_name_the_caller_so_the_message_is_actionable() -> None:
    with pytest.raises(ValueError, match="reminder destination is required"):
        resolve_destination(None, None, noun="reminder")
    with pytest.raises(ValueError, match="nag destination must be a non-empty"):
        resolve_destination("no-channel", None, noun="nag")
    with pytest.raises(ValueError, match="morning brief destination must be a non-empty"):
        resolve_destination("   ", None, noun="morning brief")


def test_payload_fallback_never_refuses_mid_delivery() -> None:
    # The destination was validated at create time; a scheduler part-way
    # through a delivery has no useful way to refuse one.
    assert destination_from_payload({"destination": "slack:D9", "chat_id": 7}) == "slack:D9"
    assert destination_from_payload({"chat_id": 7}) == "telegram:7"
