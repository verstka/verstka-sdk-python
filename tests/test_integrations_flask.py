"""Tests for Flask integration."""

from __future__ import annotations

import httpx
import respx
from flask import Flask

from verstka_sdk import (
    ContentFinalizeContext,
    ContentFinalizeResult,
    FontsCallbackResult,
    MaterialCallbackResult,
    VerstkaClient,
    VerstkaConfig,
)
from verstka_sdk.exceptions import VerstkaSignatureError
from verstka_sdk.integrations.flask import build_blueprint, register_error_handlers


class _SyncStubStorage:
    def save_media(self, filename, _temp_path, material_id, _metadata):
        return f"https://cdn.test/{material_id}/{filename}"

    def save_font_file(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"

    def save_fonts_manifest(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"


class _SyncSpyClient:
    def __init__(self) -> None:
        self.material_calls = 0
        self.fonts_calls = 0

    def process_material_callback(self, _callback_data, **_kwargs):
        self.material_calls += 1
        return MaterialCallbackResult(
            success=True,
            message="Saved successfully",
            data={"flow": "material"},
        )

    def process_fonts_callback(self, _callback_data, **_kwargs):
        self.fonts_calls += 1
        return FontsCallbackResult(
            success=True,
            message="Fonts saved successfully",
            fonts={"flow": "fonts"},
        )


def _make_app(config: VerstkaConfig) -> Flask:
    app = Flask(__name__)
    app.testing = True
    client = VerstkaClient(config)
    register_error_handlers(app)

    def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})

    app.register_blueprint(
        build_blueprint(
            client,
            storage=_SyncStubStorage(),
            on_content_finalize=on_content_finalize,
        )
    )
    return app


def _make_spy_app() -> tuple[Flask, _SyncSpyClient]:
    app = Flask(__name__)
    app.testing = True
    client = _SyncSpyClient()

    def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})

    app.register_blueprint(
        build_blueprint(
            client,  # type: ignore[arg-type]
            storage=_SyncStubStorage(),
            on_content_finalize=on_content_finalize,
        )
    )
    return app, client


@respx.mock
def test_flask_callback_happy_path(config: VerstkaConfig, sign, build_content_zip) -> None:
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
    app = _make_app(config)
    with app.test_client() as tc:
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
    body = response.get_json()
    assert body["rc"] == 1


def test_flask_callback_dispatches_fonts_event_to_fonts_flow() -> None:
    app, client = _make_spy_app()
    with app.test_client() as tc:
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
    body = response.get_json()
    assert body["rm"] == "Fonts saved successfully"
    assert body["data"]["fonts"]["flow"] == "fonts"


def test_flask_callback_dispatches_material_payload_to_material_flow() -> None:
    app, client = _make_spy_app()
    with app.test_client() as tc:
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
    assert response.get_json()["data"]["flow"] == "material"


def test_flask_invalid_signature(config: VerstkaConfig) -> None:
    app = _make_app(config)
    with app.test_client() as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "material_id": "M1",
                "content_url": "https://x.test",
                "metadata": {},
            },
            headers={"X-Verstka-Signature": "wrong"},
        )
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == "invalid_signature"


def test_flask_error_handler_reusable(config: VerstkaConfig) -> None:
    app = Flask(__name__)
    register_error_handlers(app)

    @app.get("/boom")
    def _boom():
        raise VerstkaSignatureError("bad")

    with app.test_client() as tc:
        response = tc.get("/boom")
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_signature"
