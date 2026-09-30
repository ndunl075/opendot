from __future__ import annotations

import re
import types
from typing import Annotated, Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient

from opendot_core.api import models as m
from opendot_core.api.contract import ENDPOINTS, STREAM, all_models
from opendot_core.api.fake_data import fake_json
from opendot_core.api.mock_server import create_app
from opendot_core.providers.features import FEATURES, OPT_IN_PROVIDERS, PAID_PROVIDERS

SENSITIVE = re.compile(r"key|token|secret|password|credential", re.IGNORECASE)
ENDPOINT_NAMES = {
    "provider_enabled_set", "provider_api_key_save", "provider_api_key_remove", "features_list", "feature_set",
    "spend_get", "spend_cap_set",
}


def _models_in(tp: Any) -> list[type[BaseModel]]:
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return [tp]
    if get_origin(tp) in (Union, types.UnionType, list, dict):
        return [found for arg in get_args(tp) for found in _models_in(arg)]
    return []


def _walk(model: type[BaseModel], seen: set[type[BaseModel]]) -> None:
    if model in seen:
        return
    seen.add(model)
    for field in model.model_fields.values():
        for nested in _models_in(field.annotation):
            _walk(nested, seen)


def _response_models() -> set[type[BaseModel]]:
    roots = [ep.response for ep in ENDPOINTS] + list(STREAM.server_events)
    seen: set[type[BaseModel]] = set()
    for root in roots:
        _walk(root, seen)
    return seen


def test_provider_endpoints_are_registered() -> None:
    assert ENDPOINT_NAMES <= {ep.name for ep in ENDPOINTS}


def test_model_names_match_provider_layer() -> None:
    assert set(get_args(m.FeatureName)) == set(FEATURES)
    assert set(get_args(m.PaidProviderId)) == set(PAID_PROVIDERS)
    assert set(get_args(m.OptInProviderId)) == set(OPT_IN_PROVIDERS)


def test_no_response_model_can_carry_an_api_key_or_token() -> None:
    offenders = []
    for model in _response_models():
        for name, field in model.model_fields.items():
            if SENSITIVE.search(name) and field.annotation not in (bool, int, float):
                offenders.append(f"{model.__name__}.{name}")
    assert not offenders, f"key/token/secret fields must be request-only (bool flags and numeric counters such as token counts are fine): {offenders}"


def test_the_sensitive_field_check_sees_the_response_models() -> None:
    names = {model.__name__ for model in _response_models()}
    assert {"ProviderKeyStatus", "Settings", "ProviderOptIn"} <= names
    assert any(SENSITIVE.search(n) for model in all_models().values() for n in model.model_fields)


def test_api_key_is_request_only() -> None:
    assert "api_key" in m.ProviderApiKeyRequest.model_fields
    assert set(m.ProviderKeyStatus.model_fields) == {"provider", "key_saved"}
    request = m.ProviderApiKeyRequest(api_key="sk-secret-value")
    assert "sk-secret-value" not in repr(request)
    assert "sk-secret-value" not in str(request)


def test_key_endpoints_respond_with_status_only() -> None:
    by_name = {ep.name: ep for ep in ENDPOINTS}
    assert by_name["provider_api_key_save"].response is m.ProviderKeyStatus
    assert by_name["provider_api_key_remove"].response is m.ProviderKeyStatus
    assert by_name["provider_api_key_remove"].request is None


def test_key_is_never_echoed_by_the_mock_server() -> None:
    secret = "sk-test-very-secret-123"
    with TestClient(create_app()) as client:
        save = client.put("/v1/providers/openai_key/api-key", json={"api_key": secret})
        assert save.status_code == 200
        assert secret not in save.text
        assert set(save.json()) == {"provider", "key_saved"}
        assert client.put("/v1/providers/openai_key/api-key", json={"api_key": ""}).status_code == 422
        assert client.put("/v1/providers/openai_key/api-key", json={"api_key": "x", "extra": 1}).status_code == 422
        removed = client.delete("/v1/providers/openai_key/api-key")
        assert removed.status_code == 200


def test_spend_cap_rejects_negative_and_shapes_response() -> None:
    with TestClient(create_app()) as client:
        assert client.put("/v1/providers/openrouter/spend-cap", json={"cap_usd": -1}).status_code == 422
        ok = client.put("/v1/providers/openrouter/spend-cap", json={"cap_usd": 25})
        assert ok.status_code == 200
        body = m.ProviderSpend.model_validate(ok.json())
        assert body.spent_usd >= 0
        listing = m.ProviderSpendList.model_validate(client.get("/v1/providers/spend").json())
        assert [p.provider for p in listing.providers] == list(PAID_PROVIDERS)


def test_feature_fake_data_mirrors_the_provider_layer() -> None:
    listing = m.FeatureSwitchList.model_validate(fake_json(m.FeatureSwitchList))
    assert {f.name for f in listing.features} == set(FEATURES)
    for feature in listing.features:
        spec = FEATURES[feature.name]
        assert feature.cost_warning.strip() and feature.requirement.strip()
        assert set(feature.requires_any_of) == set(spec.requires_any_of)
        assert feature.enabled is False


def test_feature_set_request_is_a_plain_boolean() -> None:
    assert set(m.FeatureSwitchSetRequest.model_fields) == {"enabled"}
    assert set(m.ProviderEnabledRequest.model_fields) == {"enabled"}
    with pytest.raises(Exception):
        m.FeatureSwitchSetRequest.model_validate({"enabled": True, "name": "x"})
