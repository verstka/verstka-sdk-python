"""Tests for FastAPI integration."""

from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from verstka_sdk import (
    AsyncVerstkaClient,
    ContentFinalizeContext,
    ContentFinalizeResult,
    FontsCallbackResult,
    MaterialCallbackResult,
    VerstkaConfig,
)
from verstka_sdk.exceptions import VerstkaSignatureError
from verstka_sdk.integrations.fastapi import (
    build_callback_router,
    install_exception_handlers,
)


class _AsyncStubStorage:
    """Minimal AsyncStorageAdapter for FastAPI integration tests."""

    async def save_media(self, filename, _temp_path, material_id, _metadata):
        return f"https://cdn.test/{material_id}/{filename}"

    async def save_font_file(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"

    async def save_fonts_manifest(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"


class _AsyncSpyClient:
    def __init__(self) -> None:
        self.material_calls = 0
        self.fonts_calls = 0

    async def process_material_callback(self, _callback_data: dict[str, Any], **_kwargs: Any):
        self.material_calls += 1
        return MaterialCallbackResult(
            success=True,
            message="Saved successfully",
            data={"flow": "material"},
        )

    async def process_fonts_callback(self, _callback_data: dict[str, Any], **_kwargs: Any):
        self.fonts_calls += 1
        return FontsCallbackResult(
            success=True,
            message="Fonts saved successfully",
            fonts={"flow": "fonts"},
        )


def _make_app(config: VerstkaConfig) -> tuple[FastAPI, AsyncVerstkaClient]:
    app = FastAPI()
    client = AsyncVerstkaClient(config)
    install_exception_handlers(app)

    async def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})

    router = build_callback_router(
        client,
        storage=_AsyncStubStorage(),
        on_content_finalize=on_content_finalize,
    )
    app.include_router(router)
    return app, client


def _make_spy_app() -> tuple[FastAPI, _AsyncSpyClient]:
    app = FastAPI()
    client = _AsyncSpyClient()

    async def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})

    router = build_callback_router(
        client,  # type: ignore[arg-type]
        storage=_AsyncStubStorage(),
        on_content_finalize=on_content_finalize,
    )
    app.include_router(router)
    return app, client


@respx.mock
def test_fastapi_callback_happy_path(config: VerstkaConfig, sign, build_content_zip) -> None:
    content_url = "https://verstka.test/download/abc"
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(
            200,
            content=build_content_zip(
                media={"a.png": b"x"},
                vms_html="<img src=dummy-a.png>",
                vms_json={"assets": {"a.png": {"clientUrl": "dummy-a.png"}}},
            ).read_bytes(),
        )
    )
    app, _ = _make_app(config)
    with TestClient(app) as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "material_id": "M1",
                "content_url": content_url,
                "metadata": {},
            },
            headers={"X-Verstka-Signature": sign("M1", content_url)},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 1
    assert "a.png" in body["data"]["vms_json"]["assets"]


def test_fastapi_callback_dispatches_fonts_event_to_fonts_flow() -> None:
    app, client = _make_spy_app()
    with TestClient(app) as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "event": "site_fonts_updated",
                "material_id": "M1",
                "content_url": "https://verstka.test/fonts",
                "metadata": {},
                "fonts": {},
            },
            headers={"X-Verstka-Signature": "sig"},
        )

    assert response.status_code == 200
    assert client.fonts_calls == 1
    assert client.material_calls == 0
    body = response.json()
    assert body["rm"] == "Fonts saved successfully"
    assert body["data"]["fonts"]["flow"] == "fonts"


def test_fastapi_callback_dispatches_material_payload_to_material_flow() -> None:
    app, client = _make_spy_app()
    with TestClient(app) as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "event": "article_updated",
                "material_id": "M1",
                "content_url": "https://verstka.test/content",
                "metadata": {},
            },
            headers={"X-Verstka-Signature": "sig"},
        )

    assert response.status_code == 200
    assert client.material_calls == 1
    assert client.fonts_calls == 0
    assert response.json()["data"]["flow"] == "material"


def test_fastapi_invalid_signature_maps_to_400(config: VerstkaConfig) -> None:
    app, _ = _make_app(config)
    with TestClient(app) as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "material_id": "M1",
                "content_url": "https://verstka.test/x",
                "metadata": {},
            },
            headers={"X-Verstka-Signature": "wrong"},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_signature"


def test_fastapi_ignores_body_signature_without_header(
    config: VerstkaConfig, sign
) -> None:
    """``signature`` in JSON is ignored; missing ``X-Verstka-Signature`` fails."""
    app, _ = _make_app(config)
    content_url = "https://verstka.test/x"
    with TestClient(app) as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "material_id": "M1",
                "content_url": content_url,
                "signature": sign("M1", content_url),
                "metadata": {},
            },
        )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_signature"


def test_install_exception_handlers_emits_json(config: VerstkaConfig) -> None:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/boom")
    async def _boom() -> Any:
        raise VerstkaSignatureError("bad")

    with TestClient(app, raise_server_exceptions=False) as tc:
        response = tc.get("/boom")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_signature"
