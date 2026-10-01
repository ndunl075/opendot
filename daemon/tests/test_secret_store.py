import pytest

from opendot_core.secret_store import SecretStoreError, SystemKeyringSecretStore


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
    assert backing[("opendot", "google-oauth-refresh-token")] == "refresh-value"


def test_store_rejects_an_empty_secret() -> None:
    with pytest.raises(ValueError, match="empty"):
        SystemKeyringSecretStore().store("google-oauth-refresh-token", "  ")


def test_a_long_secret_is_split_into_parts_that_fit_windows_credential_manager(_isolated_keyring) -> None:
    """Windows refuses values over 2560 bytes; ChatGPT's tokens are longer than that."""
    store = SystemKeyringSecretStore()
    long_value = "".join(chr(ord("a") + index % 26) for index in range(3000))
    store.store("chatgpt_plan.credentials", long_value)

    assert store.get_required("chatgpt_plan.credentials") == long_value
    assert all(len(value.encode("utf-16-le")) <= 2560 for value in _isolated_keyring.values.values())
    assert _isolated_keyring.values[("opendot", "chatgpt_plan.credentials")] == "opendot-chunked:v1:5"


def test_overwriting_and_deleting_a_long_secret_leaves_no_parts_behind(_isolated_keyring) -> None:
    store = SystemKeyringSecretStore()
    store.store("token", "x" * 3000)
    store.store("token", "y" * 700)
    assert store.get_required("token") == "y" * 700
    assert sorted(name for _, name in _isolated_keyring.values) == ["token", "token#part0", "token#part1"]

    store.store("token", "short")
    assert store.get_required("token") == "short"
    assert [name for _, name in _isolated_keyring.values] == ["token"]

    store.store("token", "z" * 1300)
    store.delete("token")
    assert _isolated_keyring.values == {}
    assert store.get_optional("token") is None


def test_a_missing_part_is_an_error_not_a_truncated_secret(_isolated_keyring) -> None:
    store = SystemKeyringSecretStore()
    store.store("token", "x" * 1300)
    del _isolated_keyring.values[("opendot", "token#part1")]
    with pytest.raises(SecretStoreError, match="incomplete"):
        store.get_optional("token")


def test_a_short_value_that_looks_like_the_marker_still_round_trips(_isolated_keyring) -> None:
    store = SystemKeyringSecretStore()
    store.store("token", "opendot-chunked:v1:3")
    assert store.get_required("token") == "opendot-chunked:v1:3"
