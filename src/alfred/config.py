"""Configuration with safe local defaults and no secrets in the database."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from .quiet_hours import QuietHours


class Settings(BaseModel):
    """Runtime settings resolved from explicit input or environment variables."""

    database_path: Path = Field(default_factory=lambda: Path(".alfred") / "alfred.db")
    quiet_hours: QuietHours = Field(default_factory=QuietHours.disabled)

    @classmethod
    def from_environment(cls, database_path: Path | None = None) -> "Settings":
        """Create settings without reading or persisting secrets."""
        configured_path = database_path or Path(
            os.environ.get("ALFRED_DB_PATH", ".alfred/alfred.db")
        )
        return cls(database_path=configured_path, quiet_hours=QuietHours.from_environment())
