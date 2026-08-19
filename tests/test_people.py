from datetime import UTC, datetime
from pathlib import Path

from alfred.connector_records import ConnectorRecordStore
from alfred.db import Database
from alfred.memory_graph import MemoryGraph
from alfred.people import PeopleService


def _event(database: Database, record_id: str, *, creator: dict | None = None, organizer: dict | None = None) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="google_calendar",
                account="primary",
                record_type="event",
                record_id=record_id,
                payload={"title": "Dinner", "creator": creator, "organizer": organizer},
                active=True,
            )


def _people(database: Database) -> list[tuple[str, bool]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT label, confirmed FROM entities WHERE entity_type = 'person' ORDER BY label"
        ).fetchall()
    return [(row["label"], bool(row["confirmed"])) for row in rows]


def test_a_calendar_organizer_becomes_an_unconfirmed_person(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "alex@example.com", "displayName": "Alex Chen"})

    result = PeopleService(database).sync()

    assert result.created == 1
    # Derived, not stated: it stays quarantined until confirmed.
    assert _people(database) == [("Alex Chen", False)]


def test_the_address_becomes_an_alias_so_either_name_resolves(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "alex@example.com", "displayName": "Alex Chen"})

    PeopleService(database).sync()

    graph = MemoryGraph(database)
    assert graph.resolve_entity_by_name("Alex Chen") is not None
    assert graph.resolve_entity_by_name("alex@example.com") is not None


def test_a_group_calendar_address_is_never_a_person(tmp_path: Path) -> None:
    """"Family Calendar" is a calendar, and inventing a colleague from it is worse
    than missing one."""
    database = Database(tmp_path / "alfred.db")
    _event(
        database,
        "e1",
        organizer={"email": "abc123@group.calendar.google.com", "displayName": "Family Calendar"},
    )

    result = PeopleService(database).sync()

    assert result.created == 0
    assert result.skipped_calendar == 1
    assert _people(database) == []


def test_an_address_already_known_as_a_calendar_is_not_a_person(tmp_path: Path) -> None:
    """This is how the owner's own address is excluded, with no configuration."""
    database = Database(tmp_path / "alfred.db")
    MemoryGraph(database).create_entity(entity_type="calendar", label="owner@example.com")
    _event(database, "e1", creator={"email": "owner@example.com"})

    result = PeopleService(database).sync()

    assert result.skipped_calendar == 1
    assert _people(database) == []


def test_a_machine_address_is_never_a_person(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", creator={"email": "noreply@example.com", "displayName": "Bookings"})

    result = PeopleService(database).sync()

    assert result.skipped_machine == 1
    assert _people(database) == []


def test_an_address_with_no_display_name_still_becomes_a_person(tmp_path: Path) -> None:
    """An address is a poor label but an honest one."""
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", creator={"email": "sam@example.com"})

    PeopleService(database).sync()

    assert _people(database) == [("sam@example.com", False)]


def test_repeat_runs_converge_instead_of_accumulating(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "alex@example.com", "displayName": "Alex Chen"})
    _event(database, "e2", organizer={"email": "alex@example.com", "displayName": "Alex Chen"})
    service = PeopleService(database)

    first = service.sync()
    second = service.sync()
    third = service.sync()

    # One person from two events, and nothing left to do afterwards.
    assert first.created == 1
    assert (second.created, second.aliased) == (0, 0)
    assert (third.created, third.aliased) == (0, 0)
    assert len(_people(database)) == 1


def test_gmail_senders_are_not_a_source_of_people(tmp_path: Path) -> None:
    """Measured against the real corpus: 26 of 29 candidates were brands, and
    the human-looking names came from bulk mailers ("Jamie Rivera" via
    invites@invites.example), which would be a false claim about identity."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                record_id="m1",
                payload={"from": "Jamie Rivera <invites@invites.example>", "subject": "Party"},
                active=True,
            )

    result = PeopleService(database).sync()

    assert result.observed == 0
    assert _people(database) == []


def test_an_existing_confirmed_person_is_not_downgraded(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    MemoryGraph(database).create_entity(entity_type="person", label="Alex Chen")
    _event(database, "e1", organizer={"email": "alex@example.com", "displayName": "Alex Chen"})

    PeopleService(database).sync()

    # Matched the existing entity and enriched it; did not create a duplicate
    # or overwrite the owner's own confirmation.
    assert _people(database) == [("Alex Chen", True)]
    assert MemoryGraph(database).resolve_entity_by_name("alex@example.com") is not None


def test_gmail_supplies_a_name_for_a_person_calendar_could_not_name(tmp_path: Path) -> None:
    """Calendar often has the address but no displayName, which would leave a
    person labelled with an identifier nobody calls them."""
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "robin@example.com"})
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                record_id="m1",
                payload={"from": "Jamie Rivera <robin@example.com>", "subject": "hi"},
                active=True,
            )

    PeopleService(database).sync()

    assert _people(database) == [("Jamie Rivera", False)]


def test_a_name_found_later_renames_a_person_without_losing_the_old_one(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "robin@example.com"})
    service = PeopleService(database)
    service.sync()
    assert _people(database) == [("robin@example.com", False)]

    # The name turns up in a later mail sync.
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                record_id="m1",
                payload={"from": "Jamie Rivera <robin@example.com>", "subject": "hi"},
                active=True,
            )
    result = service.sync()

    assert result.named == 1
    assert _people(database) == [("Jamie Rivera", False)]
    # Renaming is not forgetting: the old label still resolves.
    assert MemoryGraph(database).resolve_entity_by_name("robin@example.com") is not None
    assert service.sync().named == 0  # and it settles


def test_an_archived_message_still_supplies_a_name(tmp_path: Path) -> None:
    """A message being read says nothing about whether its sender has a name --
    the one real correspondent in the live corpus was in the archived set."""
    database = Database(tmp_path / "alfred.db")
    _event(database, "e1", organizer={"email": "robin@example.com"})
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                record_id="m1",
                payload={"from": "Jamie Rivera <robin@example.com>", "subject": "hi"},
                active=False,
            )

    PeopleService(database).sync()

    assert _people(database) == [("Jamie Rivera", False)]


def test_a_gmail_name_never_creates_someone_calendar_did_not_vouch_for(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                record_id="m1",
                payload={"from": "Amazon.com <store-news@shop.example>", "subject": "deals"},
                active=True,
            )

    PeopleService(database).sync()

    assert _people(database) == []


def _event_with_source(database: Database, record_id: str, *, email: str, event_id: str) -> None:
    """A calendar event in the immutable log, as the connector records it."""
    from alfred.events import EventStore

    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            EventStore.append(
                connection,
                source="google_calendar",
                external_id=event_id,
                occurred_at=datetime.now(UTC),
                content="Practice",
                metadata={"organizer": {"email": email}},
                sensitivity="personal",
            )


def test_people_are_found_in_history_not_only_in_current_records(tmp_path: Path) -> None:
    """Active connector records describe who is on the calendar now; the event
    log describes who ever was, which is where most people live."""
    database = Database(tmp_path / "alfred.db")
    _event_with_source(database, "e1", email="coach@example.com", event_id="evt-1")

    result = PeopleService(database).sync()

    assert result.created == 1
    assert _people(database) == [("coach@example.com", False)]


def test_organized_events_are_linked_and_repeat_runs_converge(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    for index in range(3):
        _event_with_source(database, f"e{index}", email="coach@example.com", event_id=f"evt-{index}")
    service = PeopleService(database)

    first = service.sync()
    second = service.sync()

    assert first.linked_events == 3
    assert second.linked_events == 0  # nothing left to attribute


def test_a_memory_is_about_whoever_organized_its_event(tmp_path: Path) -> None:
    from alfred.events import EventStore

    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="google_calendar",
                external_id="evt-1",
                occurred_at=datetime.now(UTC),
                content="Practice",
                metadata={"organizer": {"email": "coach@example.com"}},
                sensitivity="personal",
            )
    graph = MemoryGraph(database)
    mine = graph.remember("Practice was moved to Thursday.", source_event_id=event.id)
    unrelated = graph.remember("Unrelated note about coach@example.com in the text.")
    PeopleService(database).sync()

    coach = graph.resolve_entity_by_name("coach@example.com")
    assert coach is not None
    about = {memory.id for memory in graph.memories_about(coach.id)}

    assert mine.id in about
    # Structural, not textual: mentioning the address is not being about them.
    assert unrelated.id not in about


def test_the_owners_own_address_is_never_linked_as_a_person(tmp_path: Path) -> None:
    """772 of the real events were organized by the owner; treating those as a
    contact would make "by person" meaningless."""
    database = Database(tmp_path / "alfred.db")
    MemoryGraph(database).create_entity(entity_type="calendar", label="owner@example.com")
    _event_with_source(database, "e1", email="owner@example.com", event_id="evt-1")

    result = PeopleService(database).sync()

    assert result.skipped_calendar == 1
    assert result.linked_events == 0
    assert _people(database) == []
