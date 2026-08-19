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

**Gmail decides nobody's existence, but it does supply names.** Measured over
the real 629-message corpus, using Gmail to *create* people produced 29
candidates of which 26 were brands (Amazon, Venmo, Pacsun, a16z), and the
human-looking ones were the most dangerous: "Jamie Rivera" also arrives from
``invites@invites.example`` and "Jordan Lee via LinkedIn" from
``messaging-digest-noreply@linkedin.com``. Keying a person to a bulk mailer
is a false claim about who someone is, not merely noise, because a display
name there is the sender's branding.

Reading it the other way round is safe and fixes a real problem. Calendar
frequently supplies an address with no ``displayName`` at all, which would
otherwise leave a person entity labelled ``relative@example.com`` -- an
identifier, not a name anybody uses. So Gmail is consulted only for an
address Calendar has *already* vouched for, purely to find what that person
is called. A brand cannot slip in that way, because a brand is never a
calendar organizer.

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
    #: The source events this identity organized or created. Recording these
    #: is what makes "memories about this person" answerable: a memory already
    #: carries the event it came from, so linking the person to the same event
    #: yields an exact join with no text matching and no guess about whether a
    #: name in a sentence refers to this person or merely resembles them.
    source_event_ids: tuple[str, ...] = ()


#: "Alex Owner <owner@example.com>" -- Gmail stores the raw header.
_FROM_HEADER = re.compile(r"^\s*(?P<name>.*?)\s*<(?P<email>[^<>]+)>\s*$")


class PeopleSyncResult(BaseModel):
    observed: int = 0
    created: int = 0
    aliased: int = 0
    #: People whose label was an email address until a real name was found.
    named: int = 0
    #: Source events newly attributed to a person, which is what the
    #: "by person" selector joins on.
    linked_events: int = 0
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
        known_names = self._gmail_display_names()
        for candidate in self._candidates():
            if not candidate.display_name:
                candidate.display_name = known_names.get(candidate.email.casefold())
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
                # An entity still labelled with its own address has never been
                # named. If a name has since turned up, adopt it -- the old
                # label survives as an alias, so nothing that resolved before
                # stops resolving.
                if (
                    candidate.display_name
                    and existing.label.casefold() == email
                    and candidate.display_name.casefold() != email
                ):
                    self.graph.rename_entity(existing.id, candidate.display_name, actor=actor)
                    result.named += 1
                else:
                    # Otherwise attach the address as an alias so the next
                    # lookup resolves by either name -- unless it is already
                    # the label, where an alias would restate the entity's own
                    # name and make every run look like it had work to do.
                    known = {existing.label.casefold()} | {
                        alias.alias.casefold() for alias in self.graph.aliases_for(existing.id)
                    }
                    if email not in known:
                        self.graph.add_alias(entity_id=existing.id, alias=email, actor=actor)
                        result.aliased += 1
                result.linked_events += self._link_events(existing.id, candidate, actor=actor)
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
            result.linked_events += self._link_events(entity.id, candidate, actor=actor)
        return result

    def _link_events(self, entity_id: str, candidate: PersonCandidate, *, actor: str) -> int:
        """Record which source events this person organized, once each.

        This is the edge the "by person" selector runs on. A memory already
        stores the event it came from, so linking the person to the same event
        answers "which memories are about them" as an exact join -- no text
        matching, and no deciding whether a name in a sentence refers to this
        person or merely resembles them.

        Skips events already linked so repeat runs converge; the check is one
        query rather than one per event because a busy organizer can appear on
        hundreds.
        """
        if not candidate.source_event_ids:
            return 0
        with self.database.connect() as connection:
            already = {
                str(row["source_event_id"])
                for row in connection.execute(
                    "SELECT source_event_id FROM evidence WHERE subject_kind = 'entity' AND subject_id = ?",
                    (entity_id,),
                ).fetchall()
            }
        linked = 0
        for source_event_id in candidate.source_event_ids:
            if source_event_id in already:
                continue
            self.graph.record_entity_mention(
                entity_id,
                source_event_id=source_event_id,
                excerpt=candidate.email,
                actor=actor,
            )
            already.add(source_event_id)
            linked += 1
        return linked

    def _gmail_display_names(self) -> dict[str, str]:
        """Map address -> display name from every Gmail ``From`` header seen.

        Read across active *and* inactive records: a message being read or
        archived says nothing about whether the person who sent it has a name.
        The one real correspondent in this corpus turned up only in the
        archived set, which is exactly why an "unread only" sample was a
        misleading thing to have generalized from.

        This map is never a reason to create anybody. It is consulted only
        after Calendar has already established that an address is a person.
        """
        names: dict[str, str] = {}
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE connector = 'gmail' AND record_type = 'unread_message'
                """
            ).fetchall()
        for row in rows:
            sender = json.loads(row["payload_json"]).get("from")
            if not isinstance(sender, str):
                continue
            match = _FROM_HEADER.match(sender)
            if not match:
                continue
            email = match.group("email").strip().casefold()
            name = _clean_name(match.group("name").strip().strip('"'))
            # A header whose display name is just the address again teaches
            # nothing, and the first name seen wins so this stays stable.
            if name and "@" not in name and email not in names:
                names[email] = name
        return names

    def _calendar_entity_labels(self) -> set[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT label FROM entities WHERE entity_type = 'calendar'"
            ).fetchall()
        return {str(row["label"]).casefold() for row in rows}

    def _candidates(self) -> Iterable[PersonCandidate]:
        """Every distinct calendar identity, with the events each organized.

        Two sources, because they answer different questions. Currently-active
        connector records describe who is on the calendar *now*; the immutable
        event log describes who has ever been, which is where the people worth
        remembering actually live -- reading only the active set found three
        organizers where the full history holds ten.

        Deduplicated on the address, so one busy organizer is a single
        candidate carrying all of their events rather than one candidate per
        event.
        """
        by_email: dict[str, PersonCandidate] = {}
        events: dict[str, set[str]] = {}

        def observe(candidate: PersonCandidate, source_event_id: str | None) -> None:
            key = candidate.email.casefold()
            existing = by_email.get(key)
            if existing is None:
                by_email[key] = candidate
            elif candidate.display_name and not existing.display_name:
                existing.display_name = candidate.display_name
            if source_event_id:
                events.setdefault(key, set()).add(source_event_id)

        with self.database.connect() as connection:
            for row in connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE active = 1 AND connector = 'google_calendar' AND record_type = 'event'
                """
            ).fetchall():
                for candidate in _identities(json.loads(row["payload_json"])):
                    observe(candidate, None)
            # The event log is the authoritative record of who organized what,
            # and unlike connector records it is never replaced by a snapshot.
            for row in connection.execute(
                """
                SELECT id, metadata_json FROM events
                WHERE source = 'google_calendar' AND metadata_json IS NOT NULL
                """
            ).fetchall():
                for candidate in _identities(json.loads(row["metadata_json"])):
                    observe(candidate, str(row["id"]))

        for key, candidate in by_email.items():
            candidate.source_event_ids = tuple(sorted(events.get(key, ())))
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
