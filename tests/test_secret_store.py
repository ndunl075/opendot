import pytest

from alfred.secret_store import SecretStoreError, SystemKeyringSecretStore


def test_get_required_raises_when_the_secret_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)

    with pytest.raises(SecretStoreError, match="missing local credential-store secret"):
        SystemKeyringSecretStore().get_required("does-not-exist")


def test_store_then_get_required_round_trips_through_the_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    backing: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: backing.__setitem__((service, name), value))
    monkeypatch.setattr(keyring, "get_password", lambda service, name: backing.get((service, name)))

    store = SystemKeyringSecretStore()
    store.store("google-oauth-refresh-token", "refresh-value")

    assert store.get_required("google-oauth-refresh-token") == "refresh-value"
    assert backing[("alfred", "google-oauth-refresh-token")] == "refresh-value"


def test_store_rejects_an_empty_secret() -> None:
    with pytest.raises(ValueError, match="empty"):
        SystemKeyringSecretStore().store("google-oauth-refresh-token", "  ")
