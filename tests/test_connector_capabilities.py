"""A declaration that can drift from the code is worse than no declaration.

These tests read the source and the live schema rather than restating the
table, so the registry cannot quietly become fiction.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from alfred.connector_capabilities import (
    CONNECTOR_CAPABILITIES,
    capability_for,
    sensitive_connectors,
    writing_connectors,
)
from alfred.db import Database

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "alfred"

#: Modules whose connector_name differs from their filename, or that define
#: actions on behalf of a connector declared elsewhere.
_MODULE_TO_CONNECTOR = {
    "google_calendar": "google_calendar",
    "gmail": "gmail",
    "github": "github",
}


def test_every_declared_connector_is_unique() -> None:
    names = [item.connector for item in CONNECTOR_CAPABILITIES]

    assert len(names) == len(set(names))


def test_a_module_defining_actions_declares_that_it_writes() -> None:
    """The check that keeps this honest: if someone adds an Actions class to a
    connector, its declaration has to admit the connector can now write."""
    for module, connector in _MODULE_TO_CONNECTOR.items():
        source = (_SOURCE / f"{module}.py").read_text(encoding="utf-8")
        defines_actions = re.search(r"^class \w*Actions\b", source, re.MULTILINE) is not None
        capability = capability_for(connector)

        assert capability is not None, connector
        assert capability.writes is defines_actions, (
            f"{module}.py {'defines' if defines_actions else 'does not define'} an Actions "
            f"class, but {connector} declares writes={capability.writes}"
        )


def test_read_only_connectors_declare_no_write_actions() -> None:
    for item in CONNECTOR_CAPABILITIES:
        if not item.writes:
            assert item.write_actions == (), item.connector
        else:
            assert item.write_actions, item.connector


def test_declared_google_scopes_actually_appear_in_the_source() -> None:
    """Scopes are the security-relevant half of the declaration, so they are
    checked against the code that requests them rather than trusted."""
    requested = set()
    for path in _SOURCE.glob("*.py"):
        requested.update(re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", path.read_text(encoding="utf-8")))

    declared = {
        scope
        for item in CONNECTOR_CAPABILITIES
        for scope in item.scopes
        if scope.startswith("https://www.googleapis.com/auth/")
    }

    assert declared <= requested, f"declared but never requested: {sorted(declared - requested)}"


def test_google_health_is_the_only_sensitive_connector() -> None:
    """Section 9 tags every health value sensitive; client scopes exclude that
    tier by default, so a second one appearing silently would widen exposure."""
    assert sensitive_connectors() == ("google_health",)


def test_health_scopes_are_all_read_only() -> None:
    health = capability_for("google_health")

    assert health is not None
    assert health.writes is False
    assert all(scope.endswith(".readonly") for scope in health.scopes)


def test_writing_connectors_are_exactly_the_expected_set() -> None:
    """Pinned deliberately: a new writer should have to change this line, which
    is the moment to ask whether it belongs behind the approval boundary."""
    assert set(writing_connectors()) == {
        "google_calendar",
        "gmail",
        "github",
        "telegram",
        "slack",
    }


def test_every_connector_that_has_ever_synced_is_declared(tmp_path: Path) -> None:
    """Guards against a connector shipping without a declaration: anything that
    records sync state is something the operator can see, and should be able to
    ask what it does."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime.now(UTC).isoformat()
    # Names taken from the connector_name attributes across the package.
    for connector in ("google_calendar", "gmail", "github", "canvas_ical", "telegram"):
        with database.connect() as connection:
            with database.transaction(connection):
                connection.execute(
                    "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                    "VALUES (?, 'self', NULL, ?, NULL, ?)",
                    (connector, now, now),
                )

    with database.connect() as connection:
        synced = {row["connector"] for row in connection.execute("SELECT DISTINCT connector FROM sync_state")}

    undeclared = {name for name in synced if capability_for(name) is None}
    assert undeclared == set(), f"connectors with sync state but no declaration: {sorted(undeclared)}"
