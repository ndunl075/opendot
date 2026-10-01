"""User settings that the UI changes, stored as typed JSON values (one row per key).

Keys in use (each owner documents its value model):

- ``keep_awake``: ``api.models.KeepAwakeSettings`` (keep-awake, task 4.2)
- ``quiet_hours``: ``api.models.QuietHoursSettings``
- ``tier_overrides``: list of ``api.models.ModelTierOverride``; ``auto_top_tier``: bool
- ``style_preset``: ``api.models.StylePreset``
- ``providers``: ``providers.registry.ProviderSettings`` (enabled providers and feature switches)

Secrets never go here: API keys and tokens live in the OS keychain (``secret_store``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter

from .db import Database

T = TypeVar("T")
_SECRET_WORDS = ("token", "secret", "password", "api_key", "apikey")


class SettingsStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.migrate()

    def get(self, key: str, kind: Any, default: T) -> T:
        """The stored value for ``key`` parsed as ``kind`` (a model, ``list[Model]``, ``bool`` ...),
        or ``default`` when it is missing or no longer valid."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return TypeAdapter(kind).validate_json(row["value_json"])
        except ValueError:
            return default

    def set(self, key: str, value: Any) -> None:
        if any(word in key.lower() for word in _SECRET_WORDS):
            raise ValueError("secrets belong in the OS keychain, not in settings")
        if isinstance(value, BaseModel):
            payload = value.model_dump_json()
        else:
            payload = json.dumps(TypeAdapter(type(value)).dump_python(value, mode="json") if value is not None else None)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                    (key, payload, datetime.now(UTC).isoformat()),
                )


__all__ = ["SettingsStore"]
