"""S3 (v0.1 security review): other local users cannot read the database."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from opendot_core.db import Database

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@posix_only
def test_new_data_directory_and_database_files_are_owner_only(tmp_path: Path) -> None:
    old = os.umask(0o022)  # a typical permissive umask
    try:
        database = Database(tmp_path / "data" / "opendot.db")
        database.migrate()
        with database.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS t (x)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.commit()
            database.secure_files()
            for suffix in ("", "-wal", "-shm"):
                path = database.path.with_name(database.path.name + suffix)
                if path.exists():
                    assert stat.S_IMODE(path.stat().st_mode) == 0o600, suffix
        assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700
    finally:
        os.umask(old)


@posix_only
def test_existing_loose_database_is_tightened(tmp_path: Path) -> None:
    path = tmp_path / "opendot.db"
    path.touch()
    path.chmod(0o644)
    Database(path).migrate()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_permissions_code_is_harmless_on_windows(tmp_path: Path) -> None:
    Database(tmp_path / "x" / "opendot.db").migrate()  # must not raise anywhere


@posix_only
def test_an_existing_opendot_folder_is_tightened_but_a_user_folder_is_left_alone(tmp_path: Path) -> None:
    app_dir = tmp_path / "org.opendot.desktop"
    app_dir.mkdir(mode=0o755)
    Database(app_dir / "opendot.db").migrate()
    assert stat.S_IMODE(app_dir.stat().st_mode) == 0o700
    user_dir = tmp_path / "Documents"
    user_dir.mkdir(mode=0o755)
    Database(user_dir / "opendot.db").migrate()
    assert stat.S_IMODE(user_dir.stat().st_mode) == 0o755
