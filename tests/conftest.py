"""Shared fixtures for verstka-sdk tests."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from verstka_sdk import VerstkaConfig
from verstka_sdk.signatures import sign_material

API_SECRET = "test-secret"
API_KEY = "test-api-key"
CALLBACK_URL = "https://app.example.com/verstka/callback"


@pytest.fixture
def config() -> VerstkaConfig:
    return VerstkaConfig(
        api_key=API_KEY,
        api_secret=API_SECRET,
        callback_url=CALLBACK_URL,
        api_url="https://verstka.test/api/v2",
        max_content_size=1024 * 1024,
        request_timeout=5.0,
        download_timeout=5.0,
        debug=False,
    )


@pytest.fixture
def config_debug(config: VerstkaConfig) -> VerstkaConfig:
    return config.model_copy(update={"debug": True})


@pytest.fixture
def sign() -> Callable[[str, str], str]:
    def _sign(material_id: str, url: str) -> str:
        return sign_material(material_id, url, API_SECRET)

    return _sign


@pytest.fixture
def build_content_zip(tmp_path: Path) -> Callable[..., Path]:
    """Build a content ZIP with the expected Verstka layout."""

    def _factory(
        *,
        media: dict[str, bytes] | None = None,
        vms_json: dict[str, Any] | str | None = None,
        vms_html: str | None = None,
        extra_members: dict[str, bytes] | None = None,
    ) -> Path:
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in (media or {}).items():
                zf.writestr(f"vms_media/{name}", payload)
            if vms_json is not None:
                data = vms_json if isinstance(vms_json, str) else json.dumps(vms_json)
                zf.writestr("vms_json.json", data)
            if vms_html is not None:
                zf.writestr("vms_html.html", vms_html)
            for name, payload in (extra_members or {}).items():
                zf.writestr(name, payload)
        target = tmp_path / "content.zip"
        target.write_bytes(zip_bytes.getvalue())
        return target

    return _factory


@pytest.fixture
def build_fonts_zip(tmp_path: Path) -> Callable[..., Path]:
    """Build a fonts ZIP for `site_fonts_updated` callbacks."""

    def _factory(
        *,
        fonts: dict[str, bytes] | None = None,
        vms_fonts_json: dict[str, Any] | None = None,
        vms_fonts_css: str | None = None,
    ) -> Path:
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in (fonts or {}).items():
                zf.writestr(f"vms_fonts/{name}", payload)
            if vms_fonts_json is not None:
                zf.writestr("vms_fonts.json", json.dumps(vms_fonts_json))
            if vms_fonts_css is not None:
                zf.writestr("vms_fonts.css", vms_fonts_css)
        target = tmp_path / "fonts.zip"
        target.write_bytes(zip_bytes.getvalue())
        return target

    return _factory
