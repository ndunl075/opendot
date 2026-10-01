"""OS credential-store access; secrets never enter SQLite, Markdown, or audit logs."""

from __future__ import annotations

from typing import Protocol


class SecretStoreError(RuntimeError):
    """Raised when a required secret is absent from the local credential store."""


class SecretStore(Protocol):
    def get_required(self, name: str) -> str: ...


class SystemKeyringSecretStore:
    """Read a secret from the operating-system keyring only when a connector runs.

    Windows Credential Manager refuses a value over 2560 bytes (about 1280 characters), and
    ChatGPT's tokens are longer. A long secret is split into numbered parts; the entry under its own
    name then holds only a marker with the part count. Every backend gets the same layout.
    """

    service_name = "opendot"
    chunk_chars = 600  # 600 code points stay under 2560 bytes even at 4 UTF-16 bytes each
    _marker = "opendot-chunked:v1:"

    def get_required(self, name: str) -> str:
        value = self.get_optional(name)
        if not value:
            raise SecretStoreError(f"missing local credential-store secret: {name}")
        return value

    def get_optional(self, name: str) -> str | None:
        keyring = _keyring()
        head = keyring.get_password(self.service_name, name)
        count = self._part_count(head)
        if count is None:
            return head
        parts = [keyring.get_password(self.service_name, self._part_name(name, index)) for index in range(count)]
        if any(part is None for part in parts):
            raise SecretStoreError(f"local credential-store secret is incomplete: {name}")
        return "".join(part for part in parts if part is not None)

    def store(self, name: str, value: str) -> None:
        """Write a secret obtained by a local flow directly to the OS keyring."""
        if not value.strip():
            raise ValueError("cannot store an empty secret")
        keyring = _keyring()
        old_count = self._part_count(keyring.get_password(self.service_name, name)) or 0
        if len(value) <= self.chunk_chars and not value.startswith(self._marker):
            keyring.set_password(self.service_name, name, value)
            new_count = 0
        else:
            pieces = [value[start : start + self.chunk_chars] for start in range(0, len(value), self.chunk_chars)]
            for index, piece in enumerate(pieces):  # parts first, so the marker never points at missing parts
                keyring.set_password(self.service_name, self._part_name(name, index), piece)
            keyring.set_password(self.service_name, name, f"{self._marker}{len(pieces)}")
            new_count = len(pieces)
        for index in range(new_count, old_count):  # drop parts a longer earlier value left behind
            self._delete_entry(self._part_name(name, index))

    def delete(self, name: str) -> None:
        """Remove a temporary secret; absence is already the desired state."""
        keyring = _keyring()
        count = self._part_count(keyring.get_password(self.service_name, name)) or 0
        self._delete_entry(name)
        for index in range(count):
            self._delete_entry(self._part_name(name, index))

    def _delete_entry(self, entry: str) -> None:
        keyring = _keyring()
        try:
            keyring.delete_password(self.service_name, entry)
        except keyring.errors.PasswordDeleteError:
            return

    def _part_count(self, head: str | None) -> int | None:
        if head is None or not head.startswith(self._marker):
            return None
        count = head[len(self._marker) :]
        return int(count) if count.isdigit() else None

    @staticmethod
    def _part_name(name: str, index: int) -> str:
        return f"{name}#part{index}"


def _keyring():  # type: ignore[no-untyped-def]
    try:
        import keyring
        import keyring.errors
    except ImportError as error:
        raise SecretStoreError("keyring support is not installed") from error
    return keyring
