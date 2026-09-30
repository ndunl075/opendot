"""The newest packaged migration number, so tests do not hard-code the schema version."""

from importlib.resources import files

LATEST_SCHEMA_VERSION = max(
    int(path.name.split("_", maxsplit=1)[0])
    for path in files("opendot_core.migrations").iterdir()
    if path.name.endswith(".sql")
)
