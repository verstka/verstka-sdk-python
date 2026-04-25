"""Tests for Django REST Framework integration."""

from __future__ import annotations

import django
import httpx
import respx
from django.conf import settings

from verstka_sdk import (
    ContentFinalizeContext,
    ContentFinalizeResult,
    VerstkaClient,
    VerstkaConfig,
)
from verstka_sdk.exceptions import VerstkaSignatureError
from verstka_sdk.integrations.drf import build_callback_views, verstka_exception_handler


def _ensure_django() -> None:
    if settings.configured:
        return
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test",
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "rest_framework",
        ],
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=[],
        REST_FRAMEWORK={
            "EXCEPTION_HANDLER": "verstka_sdk.integrations.drf.verstka_exception_handler",
        },
    )
    django.setup()


_ensure_django()

from django.urls import path  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


class _SyncStubStorage:
    def save_media(self, filename, _temp_path, material_id, _metadata):
        return f"https://cdn.test/{material_id}/{filename}"

    def save_font_file(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"

    def save_fonts_manifest(self, filename, _temp_path, _material_id, _metadata):
        return f"https://cdn.test/fonts/{filename}"


def _on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json or {})


def _build_urlpatterns(cfg: VerstkaConfig):
    client = VerstkaClient(cfg)
    views = build_callback_views(
        client,
        storage=_SyncStubStorage(),
        on_content_finalize=_on_content_finalize,
    )
    return [
        path("verstka/callback/", views["callback"].as_view()),
    ]


urlpatterns: list = []


@respx.mock
def test_drf_callback_happy_path(config: VerstkaConfig, sign, build_content_zip) -> None:
    global urlpatterns
    urlpatterns = _build_urlpatterns(config)

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
    client = APIClient()
    response = client.post(
        "/verstka/callback/",
        {
            "material_id": "M1",
            "content_url": content_url,
            "metadata": {},
        },
        format="json",
        HTTP_X_VERSTKA_SIGNATURE=sign("M1", content_url),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 1


def test_drf_invalid_signature(config: VerstkaConfig) -> None:
    global urlpatterns
    urlpatterns = _build_urlpatterns(config)

    client = APIClient()
    response = client.post(
        "/verstka/callback/",
        {
            "material_id": "M1",
            "content_url": "https://x.test",
            "metadata": {},
        },
        format="json",
        HTTP_X_VERSTKA_SIGNATURE="wrong",
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_signature"


def test_drf_exception_handler_direct() -> None:
    """Call the DRF exception handler directly to verify mapping."""
    response = verstka_exception_handler(VerstkaSignatureError("bad"), context={})
    assert response is not None
    assert response.status_code == 400
    assert response.data["code"] == "invalid_signature"
