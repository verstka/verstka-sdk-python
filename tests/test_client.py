"""Tests for VerstkaClient + AsyncVerstkaClient (http layer)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from verstka_sdk import AsyncVerstkaClient, VerstkaClient, VerstkaConfig, __version__
from verstka_sdk.exceptions import VerstkaApiError, VerstkaMetadataJsonError, VerstkaVmsJsonError
from verstka_sdk.signatures import sign_material


@respx.mock
def test_get_editor_url_sync(config: VerstkaConfig) -> None:
    route = respx.post(config.session_open_url).mock(
        return_value=httpx.Response(200, json={"url": "https://editor.test/session/xyz"})
    )
    with VerstkaClient(config) as client:
        url = client.get_editor_url(
            material_id="M1",
            vms_json={"foo": "bar"},
            metadata={"user_id": 7},
        )
    assert url == "https://editor.test/session/xyz"
    request = route.calls.last.request
    body = json.loads(request.content.decode())
    assert body["api_key"] == config.api_key
    assert body["callback_url"] == config.callback_url
    assert body["material_id"] == "M1"
    assert body["metadata"]["user_id"] == 7
    assert body["metadata"]["version"] == f"python_{__version__}"
    assert body["vms_json"] == {"foo": "bar"}
    expected_sig = sign_material("M1", config.callback_url, config.api_secret)
    assert request.headers["X-Verstka-Signature"] == expected_sig


@respx.mock
def test_get_editor_url_string_vms_json(config: VerstkaConfig) -> None:
    respx.post(config.session_open_url).mock(
        return_value=httpx.Response(200, json={"url": "ok"})
    )
    with VerstkaClient(config) as client:
        client.get_editor_url(material_id="M1", vms_json=json.dumps({"x": 1}))


@respx.mock
def test_get_editor_url_invalid_json(config: VerstkaConfig) -> None:
    with VerstkaClient(config) as client:
        with pytest.raises(VerstkaVmsJsonError):
            client.get_editor_url(material_id="M1", vms_json="{not json")
        with pytest.raises(VerstkaMetadataJsonError):
            client.get_editor_url(material_id="M1", metadata="{nope")


@respx.mock
def test_get_editor_url_api_error(config: VerstkaConfig) -> None:
    respx.post(config.session_open_url).mock(
        return_value=httpx.Response(500, text="boom")
    )
    with VerstkaClient(config) as client, pytest.raises(VerstkaApiError) as exc:
        client.get_editor_url(material_id="M1")
    assert exc.value.status_code == 500


@respx.mock
def test_get_editor_url_missing_url_field(config: VerstkaConfig) -> None:
    respx.post(config.session_open_url).mock(
        return_value=httpx.Response(200, json={"error": "denied"})
    )
    with VerstkaClient(config) as client, pytest.raises(VerstkaApiError):
        client.get_editor_url(material_id="M1")


@respx.mock
async def test_get_editor_url_async(config: VerstkaConfig) -> None:
    respx.post(config.session_open_url).mock(
        return_value=httpx.Response(200, json={"url": "https://editor.test/xyz"})
    )
    async with AsyncVerstkaClient(config) as client:
        url = await client.get_editor_url(material_id="M1")
    assert url == "https://editor.test/xyz"


@respx.mock
async def test_webhook_auth_metadata(config: VerstkaConfig) -> None:
    route = respx.post(config.session_open_url).mock(
        return_value=httpx.Response(200, json={"url": "ok"})
    )
    async with AsyncVerstkaClient(config) as client:
        await client.get_editor_url(
            material_id="M1",
            metadata={"webhook_auth_user": "u", "webhook_auth_password": "p"},
        )
    body = json.loads(route.calls.last.request.content.decode())
    assert body["metadata"]["webhook_auth_user"] == "u"
    assert body["metadata"]["webhook_auth_password"] == "p"
