"""One channel-destination rule for every job store.

``chat_id`` remains a compatibility bridge for existing Telegram callers; new
integrations supply a complete ``channel:recipient`` destination. Reminders,
nags, annual dates, and brief schedules each carried their own copy of this
check, so a change to what counts as a valid destination had to be made in
four places to stay true.
"""

from __future__ import annotations

from typing import Any


def resolve_destination(
    destination: str | None, chat_id: int | None, *, noun: str
) -> str:
    """Return a validated ``channel:recipient``, naming ``noun`` on refusal."""
    if destination is None:
        if chat_id is None:
            raise ValueError(f"{noun} destination is required")
        destination = f"telegram:{chat_id}"
    if not destination.strip() or ":" not in destination:
        raise ValueError(
            f"{noun} destination must be a non-empty channel:recipient value"
        )
    return destination


def destination_from_payload(payload: dict[str, Any]) -> str:
    """Delivery target for a stored job payload.

    Unlike :func:`resolve_destination` this validates nothing: the payload was
    already checked when the job was created, and a scheduler mid-delivery has
    no useful way to refuse.
    """
    return payload.get("destination") or f"telegram:{payload['chat_id']}"
