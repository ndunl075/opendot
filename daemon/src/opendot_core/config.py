"""Configuration with safe local defaults and no secrets in the database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from .quiet_hours import QuietHours


def data_dir() -> Path:
    """The per-user folder that holds OpenDot's data: ``OPENDOT_DATA_DIR``, else the OS app-data folder.

    Never the current directory: data must not depend on where OpenDot was started, and must never
    land inside a source checkout where it could be committed by accident.
    """
    configured = os.environ.get("OPENDOT_DATA_DIR")
    if configured:
        return Path(configured)
    from .service import app_data_dir

    system = sys.platform if sys.platform in ("win32", "darwin") else "linux"
    return Path(app_data_dir(system, os.environ, str(Path.home())))  # type: ignore[arg-type]


def default_database_path() -> Path:
    configured = os.environ.get("OPENDOT_DB_PATH")
    return Path(configured) if configured else data_dir() / "opendot.db"


def chatgpt_state_path() -> Path:
    """Non-secret ChatGPT sign-in state (client registration, host id); tokens live in the keychain."""
    return data_dir() / "chatgpt_plan.json"


class Settings(BaseModel):
    """Runtime settings resolved from explicit input or environment variables."""

    database_path: Path = Field(default_factory=default_database_path)
    quiet_hours: QuietHours = Field(default_factory=QuietHours.disabled)

    @classmethod
    def from_environment(cls, database_path: Path | None = None) -> "Settings":
        """Create settings without reading or persisting secrets."""
        configured_path = database_path or default_database_path()
        return cls(database_path=configured_path, quiet_hours=QuietHours.from_environment())
