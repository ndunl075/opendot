"""Mail ranking: what matters ahead of what is bulk."""

from __future__ import annotations

from opendot_core.agent.mail import low_priority_mail, mail_rank


def test_gmail_categories_mark_bulk_mail_low_priority() -> None:
    assert low_priority_mail({"subject": "hi", "label_ids": ["INBOX", "CATEGORY_SOCIAL"]})
    assert low_priority_mail({"subject": "hi", "label_ids": ["CATEGORY_PROMOTIONS"]})
    assert not low_priority_mail({"subject": "lunch?", "label_ids": ["INBOX", "CATEGORY_PRIMARY"]})


def test_list_unsubscribe_outranks_a_personal_category() -> None:
    """Live mail showed most newsletters labeled CATEGORY_PERSONAL."""
    assert low_priority_mail(
        {"subject": "weekly picks", "label_ids": ["CATEGORY_PERSONAL"], "list_unsubscribe": "<x>"}
    )


def test_high_signal_language_wins_over_bulk_signals() -> None:
    assert not low_priority_mail(
        {"subject": "Password reset", "list_unsubscribe": "<x>", "label_ids": []}
    )
    assert not low_priority_mail(
        {"subject": "Payment failed", "snippet": "unsubscribe", "label_ids": []}
    )


def test_legacy_rows_without_labels_fall_back_to_newsletter_language() -> None:
    assert low_priority_mail({"subject": "Our newsletter", "snippet": "unsubscribe here"})
    assert low_priority_mail({"subject": "Shop now", "snippet": "20% off"})
    assert not low_priority_mail({"subject": "dinner friday", "snippet": "are you free"})


def test_ranking_puts_consequential_then_primary_then_alphabetical() -> None:
    messages = [
        {"subject": "zebra", "label_ids": ["CATEGORY_UPDATES"]},
        {"subject": "beta", "label_ids": ["CATEGORY_PRIMARY"]},
        {"subject": "Invoice overdue", "label_ids": ["CATEGORY_UPDATES"]},
        {"subject": "alpha", "label_ids": ["CATEGORY_UPDATES"]},
    ]

    ranked = sorted(messages, key=mail_rank)

    assert [message["subject"] for message in ranked] == [
        "Invoice overdue",
        "beta",
        "alpha",
        "zebra",
    ]


def test_ranking_tolerates_missing_and_malformed_labels() -> None:
    assert mail_rank({}) == (1, 1, "")
    assert mail_rank({"subject": "X", "label_ids": "CATEGORY_PRIMARY"}) == (1, 1, "x")
