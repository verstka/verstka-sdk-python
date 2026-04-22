"""Tests for Flask integration."""

from __future__ import annotations

import httpx
import respx
from flask import Flask

from verstka_sdk import (
    ContentFinalizeContext,
    ContentFinalizeResult,
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
                "signature": sign("M1", content_url),
                "metadata": {},
            },
        )
    assert response.status_code == 200
    body = response.get_json()
    assert body["rc"] == 1


def test_flask_invalid_signature(config: VerstkaConfig) -> None:
    app = _make_app(config)
    with app.test_client() as tc:
        response = tc.post(
            "/verstka/callback",
            json={
                "material_id": "M1",
                "content_url": "https://x.test",
                "signature": "wrong",
                "metadata": {},
            },
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
