from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from opendot_core.db import Database
from opendot_core.settings_store import SettingsStore


class Thing(BaseModel):
    on: bool = False
    hours: int = 0


def test_round_trip_defaults_and_secret_keys_refused(tmp_path: Path) -> None:
    store = SettingsStore(Database(tmp_path / "s.db"))
    assert store.get("thing", Thing, Thing()) == Thing()
    store.set("thing", Thing(on=True, hours=3))
    assert store.get("thing", Thing, Thing()) == Thing(on=True, hours=3)
    store.set("flags", [Thing(on=True)])
    assert store.get("flags", list[Thing], []) == [Thing(on=True)]
    store.set("auto_top_tier", True)
    assert store.get("auto_top_tier", bool, False) is True
    with pytest.raises(ValueError):
        store.set("openai_api_key", "sk-x")


def test_invalid_stored_value_falls_back_to_default(tmp_path: Path) -> None:
    database = Database(tmp_path / "s.db")
    store = SettingsStore(database)
    store.set("thing", {"on": "not a bool at all", "hours": "x"})
    assert store.get("thing", Thing, Thing(hours=7)) == Thing(hours=7)
