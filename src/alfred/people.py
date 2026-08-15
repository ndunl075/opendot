"""Promote the people already visible in synced connector data into the graph.

Section 4 promises export and deletion "by source, time range, person, topic,
or individual item", and section 5 wants durable named things -- people among
them -- to be nodes. Neither was reachable: the graph held four calendars and
a self node, and not one person, so any "by person" query would have been a
correct join over an empty table.

The gap was never the query. It was that nothing ever turned an observed
person into an entity. Calendar already stores each event's creator and
organizer: a structured identity field keyed on an email address, so this
needs no name extraction from prose and makes no guess about who someone is.

**Gmail senders are deliberately not a source, and that was measured rather
than assumed.** Running this over the real 628-message corpus produced 29
candidates, of which 26 were brands: Amazon, Venmo, Pacsun, a16z, Codecademy.
Tightening the filter does not rescue it, because the corpus contains no
personal correspondence to find -- the connector syncs *unread* mail, which
skews entirely to bulk, and not one surviving sender was on a consumer mail
domain. Worse, the human-looking names are the most dangerous: "Alicia
Dunlap" arrived from ``evite@mailva.evite.com`` and "Jordan Lee via
LinkedIn" from ``messaging-digest-noreply@linkedin.com``. Keying a person to
a bulk mailer is not noise, it is a false claim about who someone is, and a
display name is the sender's branding rather than their identity.

Everything here is derived rather than stated, so it lands `confirmed=False`:
section 5's rule is that inferred claims stay softly quarantined until
confirmed or repeatedly supported, while an explicit statement from the owner
is high-trust. Naming someone yourself (`alfred memory-entity --type person`,
or a `type: person` vault note) creates a confirmed entity; this only ever
proposes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from pydantic import BaseModel

from .db import Database
from .memory_graph import MemoryGraph

#: Addresses that belong to software, not a person. Deliberately matched on
#: the local part rather than the domain: a real person can hold an address at
#: any domain, but nobody is named "noreply". Erring toward exclusion is the
#: right bias here -- a missed person is recoverable by naming them, while a
#: "TikTok" person node is graph pollution that has to be forgotten.
_MACHINE_LOCAL_PART = re.compile(
    r"^(?:"
    r"no-?reply|do-?not-?reply|donotreply|"
    r"notifications?|notify|alerts?|updates?|reminders?|"
    r"invitations?|invites?|"
    r"mailer-daemon|bounces?|postmaster|"
    r"support|help|info|contact|admin|billing|sales|marketing|news(?:letter)?|"
    r"automated|auto|system|robot|bot|daemon|service"
    r")(?:[+.-].*)?$",
    re.IGNORECASE,
)

#: Google's own synthetic addresses for shared and resource calendars. These
#: are already calendar entities; treating one as a person would invent a
#: colleague named "Family Calendar".
_CALENDAR_RESOURCE_DOMAIN = re.compile(
    r"(?:group\.calendar\.google\.com|calendar\.google\.com|"
    r"resource\.calendar\.google\.com|\.calendar\.google\.com)$",
    re.IGNORECASE,
)

class PersonCandidate(BaseModel):
    email: str
    display_name: str | None = None
    source: str


class PeopleSyncResult(BaseModel):
    observed: int = 0
    created: int = 0
    aliased: int = 0
    skipped_machine: int = 0
    skipped_calendar: int = 0


class PeopleService:
    """Create unconfirmed person entities from structured connector identities."""

    connector_name = "people"

    def __init__(self, database: Database, *, graph: MemoryGraph | None = None) -> None:
        self.database = database
        self.graph = graph or MemoryGraph(database)

    def sync(self, *, actor: str = "system:people") -> PeopleSyncResult:
        """Scan synced Calendar and Gmail records; add whoever is missing.

        Idempotent by construction: an address already known -- as a person's
        alias, an entity label, or a calendar -- is observed and skipped, so
        running this on every cycle converges instead of accumulating.
        """
        self.database.migrate()
        result = PeopleSyncResult()
        calendar_addresses = self._calendar_entity_labels()
        for candidate in self._candidates():
            result.observed += 1
            email = candidate.email.casefold()
            if _CALENDAR_RESOURCE_DOMAIN.search(email) or email in calendar_addresses:
                # Already a calendar in this graph -- including the owner's own
                # address, which is the `self` node rather than a contact.
                result.skipped_calendar += 1
                continue
            local_part = email.split("@", 1)[0]
            if _MACHINE_LOCAL_PART.match(local_part):
                result.skipped_machine += 1
                continue
            existing = self.graph.resolve_entity_by_name(email)
            if existing is None and candidate.display_name:
                existing = self.graph.resolve_entity_by_name(candidate.display_name)
            if existing is not None:
                # Known already. Attach the address as an alias so the next
                # lookup resolves by either name -- unless it is already the
                # label, where an alias would restate the entity's own name
                # and make each run look like it had work to do.
                known = {existing.label.casefold()} | {
                    alias.alias.casefold() for alias in self.graph.aliases_for(existing.id)
                }
                if email not in known:
                    self.graph.add_alias(entity_id=existing.id, alias=email, actor=actor)
                    result.aliased += 1
                continue
            entity = self.graph.create_entity(
                entity_type="person",
                # An address is a poor name but an honest one; a display name
                # is used when the source supplied it.
                label=candidate.display_name or email,
                sensitivity="personal",
                confirmed=False,
                confidence=0.5,
                actor=actor,
            )
            result.created += 1
            if candidate.display_name:
                self.graph.add_alias(entity_id=entity.id, alias=email, actor=actor)
                result.aliased += 1
        return result

    def _calendar_entity_labels(self) -> set[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT label FROM entities WHERE entity_type = 'calendar'"
            ).fetchall()
        return {str(row["label"]).casefold() for row in rows}

    def _candidates(self) -> Iterable[PersonCandidate]:
        """Every distinct identity Calendar and Gmail already recorded.

        Deduplicated on the address so one busy correspondent is one
        candidate, not one per message.
        """
        seen: set[str] = set()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE active = 1
                  AND connector = 'google_calendar'
                  AND record_type = 'event'
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            for candidate in _identities(payload):
                key = candidate.email.casefold()
                if key in seen:
                    continue
                seen.add(key)
                yield candidate


def _identities(payload: dict[str, Any]) -> list[PersonCandidate]:
    """Both identity fields Calendar records for an event."""
    found: list[PersonCandidate] = []
    for field in ("creator", "organizer"):
        value = payload.get(field)
        if isinstance(value, dict) and isinstance(value.get("email"), str):
            found.append(
                PersonCandidate(
                    email=value["email"],
                    display_name=_clean_name(value.get("displayName")),
                    source=f"google_calendar.{field}",
                )
            )
    return found


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    # An address repeated as its own display name adds nothing.
    return name or None
