"""Tests for Django integration (async views + exception middleware)."""

from __future__ import annotations

import json

import django
import httpx
import respx
from django.conf import settings
from django.test import AsyncClient
from django.urls import clear_url_caches, path

from verstka_sdk import (
    AsyncVerstkaClient,
    ContentFinalizeContext,
    ContentFinalizeResult,
    FontsCallbackResult,
    MaterialCallbackResult,
    VerstkaConfig,
)
from verstka_sdk.integrations.django import build_callback_views


def _ensure_django() -> None:
    if settings.configured:
        return
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test",
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=[],
    )
    django.setup()


_ensure_django()

_CLIENT_HOLDER: dict[str, object] = {}


class _AsyncStubStorage:
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

    async def process_material_callback(self, _callback_data, **_kwargs):
        self.material_calls += 1
        return MaterialCallbackResult(
            success=True,
            message="Saved successfully",
            data={"flow": "material"},
        )

    async def process_fonts_callback(self, _callback_data, **_kwargs):
        self.fonts_calls += 1
        return FontsCallbackResult(
            success=True,
            message="Fonts saved successfully",
            fonts={"flow": "fonts"},
        )


async def _on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})


def _build_urlpatterns(cfg: VerstkaConfig):
    client = AsyncVerstkaClient(cfg)
    _CLIENT_HOLDER["client"] = client
    views = build_callback_views(
        client,
        storage=_AsyncStubStorage(),
        on_content_finalize=_on_content_finalize,
    )
    return [
        path("verstka/callback/", views["callback"]),
        path("verstka/fonts-callback/", views["fonts_callback"]),
    ]


def _build_spy_urlpatterns(client: _AsyncSpyClient):
    views = build_callback_views(
        client,  # type: ignore[arg-type]
        storage=_AsyncStubStorage(),
        on_content_finalize=_on_content_finalize,
    )
    return [
        path("verstka/callback/", views["callback"]),
        path("verstka/fonts-callback/", views["fonts_callback"]),
    ]


urlpatterns: list = []


def _set_urlpatterns(patterns: list) -> None:
    global urlpatterns
    urlpatterns = patterns
    settings.ROOT_URLCONF = __name__
    clear_url_caches()


@respx.mock
async def test_django_callback_happy_path(config: VerstkaConfig, sign, build_content_zip) -> None:
    _set_urlpatterns(_build_urlpatterns(config))

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
    client = AsyncClient()
    response = await client.post(
        "/verstka/callback/",
        data=json.dumps(
            {
                "material_id": "M1",
                "content_url": content_url,
                "metadata": {},
            }
        ),
        content_type="application/json",
        headers={"X-Verstka-Signature": sign("M1", content_url)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 1


async def test_django_callback_dispatches_fonts_event_to_fonts_flow() -> None:
    spy = _AsyncSpyClient()
    _set_urlpatterns(_build_spy_urlpatterns(spy))

    client = AsyncClient()
    response = await client.post(
        "/verstka/callback/",
        data=json.dumps(
            {
                "event": "site_fonts_updated",
                "material_id": "M1",
                "content_url": "https://verstka.test/fonts",
                "metadata": {},
                "fonts": {},
            }
        ),
        content_type="application/json",
        headers={"X-Verstka-Signature": "sig"},
    )

    assert response.status_code == 200
    assert spy.fonts_calls == 1
    assert spy.material_calls == 0
    body = response.json()
    assert body["rm"] == "Fonts saved successfully"
    assert body["data"]["fonts"]["flow"] == "fonts"


async def test_django_callback_dispatches_material_payload_to_material_flow() -> None:
    spy = _AsyncSpyClient()
    _set_urlpatterns(_build_spy_urlpatterns(spy))

    client = AsyncClient()
    response = await client.post(
        "/verstka/callback/",
        data=json.dumps(
            {
                "event": "article_updated",
                "material_id": "M1",
                "content_url": "https://verstka.test/content",
                "metadata": {},
            }
        ),
        content_type="application/json",
        headers={"X-Verstka-Signature": "sig"},
    )

    assert response.status_code == 200
    assert spy.material_calls == 1
    assert spy.fonts_calls == 0
    assert response.json()["data"]["flow"] == "material"


async def test_django_invalid_signature(config: VerstkaConfig) -> None:
    _set_urlpatterns(_build_urlpatterns(config))

    client = AsyncClient()
    response = await client.post(
        "/verstka/callback/",
        data=json.dumps(
            {
                "material_id": "M1",
                "content_url": "https://x.test",
                "metadata": {},
            }
        ),
        content_type="application/json",
        headers={"X-Verstka-Signature": "wrong"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_signature"


async def test_django_not_allowed_on_get(config: VerstkaConfig) -> None:
    _set_urlpatterns(_build_urlpatterns(config))

    client = AsyncClient()
    response = await client.get("/verstka/callback/")
    assert response.status_code == 405
