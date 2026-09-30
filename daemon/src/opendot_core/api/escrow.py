"""In-memory holding place for one-time approval tokens.

The UI never sees a token. When the user approves, the server puts the token here and the
agent side takes it when it executes the approved action. Nothing is persisted, logged or
returned to a client; ``repr`` hides the contents.
"""

from __future__ import annotations

import threading


class TokenEscrow:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, approval_id: str, token: str) -> None:
        with self._lock:
            self._tokens[approval_id] = token

    def put_if_absent(self, approval_id: str, token: str) -> bool:
        """Store the token unless one is already held; True when this call stored it."""
        with self._lock:
            if approval_id in self._tokens:
                return False
            self._tokens[approval_id] = token
            return True

    def get(self, approval_id: str) -> str | None:
        """Look at a token without removing it."""
        with self._lock:
            return self._tokens.get(approval_id)

    def take(self, approval_id: str) -> str | None:
        """Remove and return a token, so it can only be handed over once."""
        with self._lock:
            return self._tokens.pop(approval_id, None)

    def discard(self, approval_id: str) -> None:
        with self._lock:
            self._tokens.pop(approval_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tokens)

    def __repr__(self) -> str:
        return f"TokenEscrow(size={len(self)})"
