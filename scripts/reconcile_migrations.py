"""Reconcile a database whose migration history diverged from this build.

A database can record a migration this build does not ship -- work that was
applied locally and never reached the branch now checked out. That is harmless
until one of those records holds a version number a packaged migration also
claims, because the version is the primary key: the packaged file can never be
recorded, so ``migrate()`` fails and Alfred will not start.

Reports by default and changes nothing. ``--apply`` removes only the records
whose files this build does not have, which is what unblocks ``migrate()``.
Tables left behind by those migrations are reported but never dropped unless
named with ``--drop-table``, and a drop is refused if the table holds rows.

    python scripts/reconcile_migrations.py
    python scripts/reconcile_migrations.py --apply
    python scripts/reconcile_migrations.py --apply --drop-table habits
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)",
    re.IGNORECASE,
)


def _packaged_migrations() -> dict[str, Path]:
    root = Path(__file__).resolve().parent.parent / "src" / "alfred" / "migrations"
    return {path.name: path for path in sorted(root.glob("*.sql"))}


def _version_of(filename: str) -> int:
    return int(filename.split("_", maxsplit=1)[0])


def _tables_created_by(migrations: dict[str, Path]) -> set[str]:
    names: set[str] = set()
    for path in migrations.values():
        names.update(match.group(1) for match in _CREATE_TABLE.finditer(path.read_text(encoding="utf-8")))
    return names


def _unaccounted_tables(connection: sqlite3.Connection, expected: set[str]) -> list[tuple[str, int]]:
    """Tables present that no packaged migration creates, with their row counts.

    An FTS5 virtual table brings its own shadow tables (``memory_fts_data`` and
    friends), so anything prefixed by an expected name is treated as belonging
    to it rather than reported as a stray.
    """
    present = [
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    stray: list[tuple[str, int]] = []
    for name in present:
        if name in expected or name == "schema_migrations" or name.startswith("sqlite_"):
            continue
        if any(name.startswith(f"{owner}_") for owner in expected):
            continue
        count = connection.execute(f'SELECT COUNT(*) AS count FROM "{name}"').fetchone()["count"]
        stray.append((name, int(count)))
    return stray


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", nargs="?", help="path to alfred.db (default: ALFRED_DB_PATH or .alfred/alfred.db)")
    parser.add_argument("--apply", action="store_true", help="delete records this build has no migration for")
    parser.add_argument("--drop-table", action="append", default=[], metavar="NAME",
                        help="also drop this leftover table; refused unless it is empty")
    args = parser.parse_args(argv)

    path = Path(args.database or os.environ.get("ALFRED_DB_PATH") or ".alfred/alfred.db")
    if not path.exists():
        print(f"no database at {path}; pass the path or set ALFRED_DB_PATH")
        return 1

    migrations = _packaged_migrations()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "schema_migrations" not in tables:
        print(f"{path} has no schema_migrations table; nothing to reconcile.")
        return 0

    recorded = [
        (int(row["version"]), str(row["filename"]))
        for row in connection.execute("SELECT version, filename FROM schema_migrations ORDER BY version")
    ]
    print(f"database: {path}")
    print(f"this build ships {len(migrations)} migrations, through {max(migrations, default='none')}")
    print(f"this database records {len(recorded)}, through version {max((v for v, _ in recorded), default=0)}")

    foreign = [(version, name) for version, name in recorded if name not in migrations]
    if not foreign:
        print("\nno divergence: every recorded migration is one this build ships.")
    else:
        print("\nrecorded here, absent from this build:")
        for version, name in foreign:
            claimant = next((n for n in migrations if _version_of(n) == version), None)
            collision = f"  <-- blocks {claimant}" if claimant else ""
            print(f"  version {version}: {name}{collision}")

    stray = _unaccounted_tables(connection, _tables_created_by(migrations))
    if stray:
        print("\ntables no packaged migration creates:")
        for name, count in stray:
            note = "empty" if count == 0 else f"{count} row{'s' if count != 1 else ''} -- KEEP"
            print(f"  {name} ({note})")

    if not args.apply:
        print("\nreport only; nothing changed. Re-run with --apply to remove the records above.")
        return 0

    if not foreign and not args.drop_table:
        print("\nnothing to apply.")
        return 0

    counts = dict(stray)
    for name in args.drop_table:
        if name not in counts:
            print(f"\nrefusing to drop {name}: it is not a leftover table on this database.")
            return 1
        if counts[name]:
            rows = counts[name]
            print(f"\nrefusing to drop {name}: it holds {rows} row{'s' if rows != 1 else ''}.")
            return 1

    connection.execute("BEGIN IMMEDIATE")
    try:
        for version, name in foreign:
            connection.execute("DELETE FROM schema_migrations WHERE version = ? AND filename = ?", (version, name))
            print(f"removed record: version {version} ({name})")
        for name in args.drop_table:
            connection.execute(f'DROP TABLE "{name}"')
            print(f"dropped empty table: {name}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    remaining = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()["version"]
    print(f"\ndone. recorded version is now {remaining}.")
    print("Start Alfred and the packaged migrations will apply the rest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
