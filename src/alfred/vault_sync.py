"""Reachability check for the self-hosted CouchDB behind optional mobile sync.

Section 5's "Phone access without paying for Obsidian" calls for a
self-hosted WebDAV/CouchDB service backing a vetted, mobile-compatible
open-source community adapter (a plugin like Self-hosted LiveSync running
inside Obsidian on desktop and phone) -- see ``deploy/couchdb/`` for that
server's setup.

Alfred's own Python code never talks to CouchDB for anything else: the
actual sync -- reading and writing ``alfred-vault/`` files, replicating
them to a phone -- is done entirely by that third-party plugin, not by
Alfred. This module exists only so an operator can confirm the server side
is actually reachable, the same way `connector-status` reports on every
other external dependency, without needing Alfred to become a CouchDB
client for anything real.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel


class VaultSyncStatus(BaseModel):
    reachable: bool
    couchdb_version: str | None = None
    error: str | None = None


def check_couchdb(base_url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 5.0) -> VaultSyncStatus:
    """Confirm the self-hosted CouchDB instance answers, without touching any document.

    Deliberately unauthenticated: CouchDB's root endpoint returns a welcome
    payload with its version to anyone, so this needs no credential and
    logs none -- it only proves the process is up and speaking CouchDB,
    which is all "is my mobile sync server reachable" requires.
    """
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout), transport=transport) as client:
            response = client.get(base_url.rstrip("/") + "/")
    except httpx.HTTPError as error:
        return VaultSyncStatus(reachable=False, error=error.__class__.__name__)
    if response.status_code >= 400:
        return VaultSyncStatus(reachable=False, error=f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return VaultSyncStatus(reachable=False, error="response was not JSON")
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("couchdb") != "Welcome":
        return VaultSyncStatus(reachable=False, error="response was not a CouchDB welcome payload")
    return VaultSyncStatus(reachable=True, couchdb_version=version if isinstance(version, str) else None)
