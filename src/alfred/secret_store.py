"""OS credential-store access; secrets never enter SQLite, Markdown, or audit logs."""

from __future__ import annotations

from typing import Protocol


class SecretStoreError(RuntimeError):
    """Raised when a required secret is absent from the local credential store."""


class SecretStore(Protocol):
    def get_required(self, name: str) -> str: ...


class SystemKeyringSecretStore:
    """Read a secret from the operating-system keyring only when a connector runs."""

    service_name = "alfred"

    def get_required(self, name: str) -> str:
        value = self.get_optional(name)
        if not value:
            raise SecretStoreError(f"missing local credential-store secret: {name}")
        return value

    def get_optional(self, name: str) -> str | None:
        try:
            import keyring
        except ImportError as error:
            raise SecretStoreError("keyring support is not installed") from error
        return keyring.get_password(self.service_name, name)

    def store(self, name: str, value: str) -> None:
        """Write a secret obtained by a local flow directly to the OS keyring."""
        if not value.strip():
            raise ValueError("cannot store an empty secret")
        try:
            import keyring
        except ImportError as error:
            raise SecretStoreError("keyring support is not installed") from error
        keyring.set_password(self.service_name, name, value)

    def delete(self, name: str) -> None:
        """Remove a temporary secret; absence is already the desired state."""
        try:
            import keyring
        except ImportError as error:
            raise SecretStoreError("keyring support is not installed") from error
        try:
            keyring.delete_password(self.service_name, name)
        except keyring.errors.PasswordDeleteError:
            return
