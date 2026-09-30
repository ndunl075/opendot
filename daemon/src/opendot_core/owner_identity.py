"""Who the owner is, learned from their own sent mail.

Alfred wrote an email that introduced itself as "a personal assistant that
helps [name] manage emails" -- the placeholder shipped intact, and only the
approval preview caught it before it reached the owner's mother.

The cause is that Alfred did not know. The `self` entity is labelled "Alfred
owner" with no properties, so nothing in the prompt could tell the model whose
assistant it is, and a model asked to write a letter with no name available
does the reasonable thing and leaves a slot.

Sent mail answers it without guessing. The From header on the owner's own
outbound messages is how they already sign their email -- 195 of them read
"Alex Owner <...>" on the live account -- so it is the owner's own choice
of name rather than an inference from an address. Calendar `displayName` was
the other candidate and is much worse: the owner's calendars are named
"FAMILY CAR", "Family", and "Holidays in United States", none of which
is a person.

Read-only, cached per process, and absent rather than wrong when there is no
sent mail yet -- a prompt with no name is recoverable, a prompt with the
wrong name signs letters as somebody else.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel

from .db import Database

#: How many outbound messages to consider. The header is stable, so this only
#: needs enough to outvote a one-off alias.
_SAMPLE = 300

#: "Alex Owner <a@b.com>" -> name, address.
_FROM_HEADER = re.compile(r"^\s*(?P<name>[^<]*?)\s*<(?P<address>[^>]+)>\s*$")


class OwnerIdentity(BaseModel):
    #: The owner's own display name, or None when nothing has been sent yet.
    name: str | None = None
    address: str | None = None

    def prompt_line(self) -> str:
        """One line for the runtime prompt, or empty when nothing is known."""
        if not self.name:
            return ""
        who = f"{self.name} <{self.address}>" if self.address else self.name
        return (
            f"you work for {who}. write and sign mail as them, in the first person "
            f"on their behalf. never write a placeholder like [name] or [your name]: "
            f"if you do not know something a letter needs, ask before proposing it."
        )


def owner_identity(database: Database) -> OwnerIdentity:
    """Read the owner's name from the From header of their own sent mail."""
    database.migrate()
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT json_extract(metadata_json, '$.from') AS sender
            FROM events
            WHERE source = 'gmail'
              AND json_extract(metadata_json, '$.outbound') = 1
              AND sender IS NOT NULL
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (_SAMPLE,),
        ).fetchall()

    seen: Counter[tuple[str, str]] = Counter()
    for row in rows:
        match = _FROM_HEADER.match(str(row["sender"] or ""))
        if not match:
            continue
        name = match.group("name").strip().strip('"').strip()
        # A From header whose "name" is just the address again carries no
        # name at all, and signing "owner@example.com," would be worse
        # than signing nothing.
        if not name or "@" in name:
            continue
        seen[(name, match.group("address").strip())] += 1
    if not seen:
        return OwnerIdentity()
    (name, address), _count = seen.most_common(1)[0]
    return OwnerIdentity(name=name, address=address)


#: Placeholders a model leaves when it lacks a fact. Matched case-insensitively
#: on the bracketed forms only, so ordinary prose in square brackets -- a
#: quoted "[sic]", a citation -- is not treated as an unfilled slot.
_PLACEHOLDER = re.compile(
    r"\[(?:\s*(?:your |my |the )?"
    r"(?:name|full name|first name|last name|email|address|phone|number|date|"
    r"company|title|role|position|insert[^\]]*|placeholder[^\]]*|x{2,})\s*)\]",
    re.IGNORECASE,
)


def unfilled_placeholders(text: str) -> list[str]:
    """Every unfilled slot in a proposed message, in order.

    A letter that reaches the owner's mother introducing Alfred as the
    assistant of "[name]" is not a small blemish: it is unrecoverable once
    sent, and the owner approving a preview is not a reliable filter because
    the whole point of the preview is that it is usually fine.
    """
    return [match.group(0) for match in _PLACEHOLDER.finditer(text or "")]
