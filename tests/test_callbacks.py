"""Tests for CallbackProcessor (shared sync/async engine)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from verstka_sdk import (
    ContentFinalizeContext,
    ContentFinalizeResult,
    ContentPreSaveContext,
    FontsFinalizeContext,
    FontsFinalizeResult,
    FontsPreSaveContext,
    PreSaveDecision,
    VerstkaConfig,
)
from verstka_sdk.callbacks import CallbackProcessor
from verstka_sdk.exceptions import VerstkaCallbackDataError, VerstkaSignatureError


def _callback_payload(
    *,
    sign,
    content_url: str,
    material_id: str = "M1",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "material_id": material_id,
        "content_url": content_url,
        "signature": sign(material_id, content_url),
        "metadata": metadata or {},
    }


class RecordingMediaStorage:
    """StorageAdapter stub that only cares about media."""

    def __init__(self) -> None:
        self.media_calls: list[tuple[str, str, dict[str, Any]]] = []

    def save_media(self, filename, temp_path, material_id, metadata):
        del temp_path
        self.media_calls.append((filename, material_id, dict(metadata)))
        return f"https://cdn.test/{material_id}/{filename}"

    def save_font_file(self, filename, temp_path, material_id, metadata):
        raise AssertionError("save_font_file should not be called in material flow")

    def save_fonts_manifest(self, filename, temp_path, material_id, metadata):
        raise AssertionError("save_fonts_manifest should not be called in material flow")


class AsyncRecordingMediaStorage:
    """AsyncStorageAdapter stub for material flow."""

    def __init__(self) -> None:
        self.media_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def save_media(self, filename, temp_path, material_id, metadata):
        del temp_path
        self.media_calls.append((filename, material_id, dict(metadata)))
        return f"https://cdn.test/{material_id}/{filename}"

    async def save_font_file(self, filename, temp_path, material_id, metadata):
        raise AssertionError("save_font_file should not be called in material flow")

    async def save_fonts_manifest(self, filename, temp_path, material_id, metadata):
        raise AssertionError("save_fonts_manifest should not be called in material flow")


class LocalFontStorage:
    """Sync storage that copies fonts + manifests to a target directory."""

    def __init__(self, target_dir: Path) -> None:
        self.target_dir = target_dir
        self.target_dir.mkdir(parents=True, exist_ok=True)

    def save_media(self, filename, temp_path, material_id, metadata):
        raise AssertionError("save_media should not be called in fonts flow")

    def save_font_file(self, filename, temp_path, material_id, metadata):
        del material_id, metadata
        target = self.target_dir / filename
        shutil.copy2(temp_path, target)
        return f"https://cdn.test/fonts/{filename}"

    def save_fonts_manifest(self, filename, temp_path, material_id, metadata):
        del material_id, metadata
        target = self.target_dir / filename
        shutil.copy2(temp_path, target)
        return f"https://cdn.test/fonts/{filename}"


class AsyncLocalFontStorage:
    """Async wrapper around :class:`LocalFontStorage`."""

    def __init__(self, target_dir: Path) -> None:
        self._sync = LocalFontStorage(target_dir)

    async def save_media(self, filename, temp_path, material_id, metadata):
        return self._sync.save_media(filename, temp_path, material_id, metadata)

    async def save_font_file(self, filename, temp_path, material_id, metadata):
        return self._sync.save_font_file(filename, temp_path, material_id, metadata)

    async def save_fonts_manifest(self, filename, temp_path, material_id, metadata):
        return self._sync.save_fonts_manifest(filename, temp_path, material_id, metadata)


# --------------------------------------------------------------------------- #
# Material callback                                                           #
# --------------------------------------------------------------------------- #

@respx.mock
async def test_process_material_callback_happy_path(
    config: VerstkaConfig, sign, build_content_zip
) -> None:
    content_url = "https://verstka.test/download/abc"
    zip_bytes = build_content_zip(
        media={"hero.png": b"img"},
        vms_json={"assets": {"hero.png": {"clientUrl": "dummy-hero.png"}}},
        vms_html="<p><img src=dummy-hero.png></p>",
    ).read_bytes()

    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )

    storage = AsyncRecordingMediaStorage()
    captured: dict[str, Any] = {}

    async def on_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        captured["ctx"] = ctx
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)

    processor = CallbackProcessor(config)
    result = await processor.process_material_callback_async(
        _callback_payload(sign=sign, content_url=content_url, metadata={"site": "s1"}),
        storage=storage,
        on_finalize=on_finalize,
    )

    ctx = captured["ctx"]
    assert result.success is True
    assert storage.media_calls == [("hero.png", "M1", {"site": "s1"})]
    assert "dummy-hero.png" not in ctx.vms_html
    assert "https://cdn.test/M1/hero.png" in ctx.vms_html
    assert ctx.vms_json["assets"]["hero.png"]["clientUrl"] == "https://cdn.test/M1/hero.png"
    assert ctx.saved_media_urls == {"hero.png": "https://cdn.test/M1/hero.png"}
    response = result.to_response()
    assert response["rc"] == 1
    assert response["data"]["vms_json"] == ctx.vms_json


@respx.mock
async def test_process_material_callback_rejects_bad_signature(config: VerstkaConfig) -> None:
    content_url = "https://verstka.test/download/x"
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=b"")
    )
    payload = {
        "material_id": "M1",
        "content_url": content_url,
        "signature": "wrong",
        "metadata": {},
    }

    processor = CallbackProcessor(config)

    async def _never(_ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        raise AssertionError("on_finalize must not be called")

    with pytest.raises(VerstkaSignatureError):
        await processor.process_material_callback_async(
            payload,
            storage=AsyncRecordingMediaStorage(),
            on_finalize=_never,
        )


async def test_process_material_callback_requires_material_id(
    config: VerstkaConfig, sign
) -> None:
    processor = CallbackProcessor(config)
    payload = {
        "material_id": "",
        "content_url": "",
        "signature": sign("", ""),
        "metadata": {},
    }

    async def _never(_ctx):
        raise AssertionError("on_finalize must not be called")

    with pytest.raises(VerstkaCallbackDataError):
        await processor.process_material_callback_async(
            payload,
            storage=AsyncRecordingMediaStorage(),
            on_finalize=_never,
        )


async def test_process_material_callback_finalize_failure(
    config: VerstkaConfig, sign
) -> None:
    processor = CallbackProcessor(config)
    payload = _callback_payload(sign=sign, content_url="")

    async def on_finalize(_ctx):
        return ContentFinalizeResult(success=False)

    result = await processor.process_material_callback_async(
        payload,
        storage=AsyncRecordingMediaStorage(),
        on_finalize=on_finalize,
    )
    assert result.success is False
    assert result.to_response()["rc"] == 0


@respx.mock
def test_process_material_callback_sync(
    config: VerstkaConfig, sign, build_content_zip
) -> None:
    content_url = "https://verstka.test/download/abc"
    zip_bytes = build_content_zip(
        media={"a.png": b"b"},
        vms_html="<img src=dummy-a.png>",
        vms_json={"assets": {"a.png": {"clientUrl": "dummy-a.png"}}},
    ).read_bytes()
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )
    storage = RecordingMediaStorage()

    def on_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)

    processor = CallbackProcessor(config)
    result = processor.process_material_callback_sync(
        _callback_payload(sign=sign, content_url=content_url),
        storage=storage,
        on_finalize=on_finalize,
    )
    assert result.success is True
    assert storage.media_calls == [("a.png", "M1", {})]


# --------------------------------------------------------------------------- #
# Fonts callback                                                              #
# --------------------------------------------------------------------------- #

@respx.mock
async def test_process_fonts_callback(
    config: VerstkaConfig, sign, build_fonts_zip, tmp_path: Path
) -> None:
    content_url = "https://verstka.test/fonts/z"
    zip_bytes = build_fonts_zip(
        fonts={"Inter-Regular.woff2": b"FONT"},
        vms_fonts_json={"families": []},
        vms_fonts_css="@font-face { src: url(dummy-Inter-Regular.woff2); }",
    ).read_bytes()
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )

    storage = AsyncLocalFontStorage(tmp_path / "fonts")

    fonts_payload = {
        "css": {"id": "vms_fonts.css"},
        "list": [
            {
                "variants": [
                    {"files": {"woff2": {"id": "Inter-Regular.woff2"}}}
                ]
            }
        ],
    }
    callback = {
        "material_id": "site-1",
        "content_url": content_url,
        "signature": sign("site-1", content_url),
        "metadata": {"tenant": "t42"},
        "fonts": fonts_payload,
    }

    captured: dict[str, Any] = {}

    async def on_finalize(ctx: FontsFinalizeContext) -> FontsFinalizeResult:
        captured["ctx"] = ctx
        return FontsFinalizeResult(success=True, fonts=ctx.fonts)

    processor = CallbackProcessor(config)
    result = await processor.process_fonts_callback_async(
        callback,
        storage=storage,
        on_finalize=on_finalize,
    )

    ctx = captured["ctx"]
    assert result.success is True
    assert ctx.material_id == "site-1"
    assert ctx.metadata == {"tenant": "t42"}
    assert ctx.saved_font_urls == {
        "Inter-Regular.woff2": "https://cdn.test/fonts/Inter-Regular.woff2"
    }
    assert ctx.css_url == "https://cdn.test/fonts/vms_fonts.css"
    assert ctx.json_url == "https://cdn.test/fonts/vms_fonts.json"
    assert (
        result.fonts["list"][0]["variants"][0]["files"]["woff2"]["clientUrl"]
        == "https://cdn.test/fonts/Inter-Regular.woff2"
    )
    assert result.fonts["css"]["clientUrl"] == "https://cdn.test/fonts/vms_fonts.css"
    # CSS content must have dummy-* rewritten before being persisted.
    css_on_disk = (tmp_path / "fonts" / "vms_fonts.css").read_text()
    assert "dummy-Inter-Regular.woff2" not in css_on_disk
    assert "https://cdn.test/fonts/Inter-Regular.woff2" in css_on_disk


@respx.mock
async def test_process_fonts_callback_returns_default_fonts_when_finalize_omits_them(
    config: VerstkaConfig, sign, build_fonts_zip, tmp_path: Path
) -> None:
    content_url = "https://verstka.test/fonts/zz"
    zip_bytes = build_fonts_zip(
        fonts={"A.woff2": b"f"},
        vms_fonts_json={"families": []},
        vms_fonts_css="/* empty */",
    ).read_bytes()
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )

    storage = AsyncLocalFontStorage(tmp_path / "out")
    callback = {
        "material_id": "site-2",
        "content_url": content_url,
        "signature": sign("site-2", content_url),
        "fonts": {"list": []},
    }

    async def on_finalize(_ctx):
        return FontsFinalizeResult(success=True)  # fonts=None -> fallback to ctx.fonts

    processor = CallbackProcessor(config)
    result = await processor.process_fonts_callback_async(
        callback,
        storage=storage,
        on_finalize=on_finalize,
    )
    assert result.fonts == {"list": []}


@respx.mock
async def test_process_fonts_callback_without_on_finalize(
    config: VerstkaConfig, sign, build_fonts_zip, tmp_path: Path
) -> None:
    """Fonts flow must work without ``on_finalize``: SDK persists via storage only."""
    content_url = "https://verstka.test/fonts/no-finalize"
    zip_bytes = build_fonts_zip(
        fonts={"Inter-Regular.woff2": b"FONT"},
        vms_fonts_json={"families": []},
        vms_fonts_css="@font-face { src: url(dummy-Inter-Regular.woff2); }",
    ).read_bytes()
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )

    storage = AsyncLocalFontStorage(tmp_path / "fonts-only")
    fonts_payload = {
        "css": {"id": "vms_fonts.css"},
        "list": [
            {"variants": [{"files": {"woff2": {"id": "Inter-Regular.woff2"}}}]},
        ],
    }
    callback = {
        "material_id": "site-3",
        "content_url": content_url,
        "signature": sign("site-3", content_url),
        "fonts": fonts_payload,
    }

    processor = CallbackProcessor(config)
    result = await processor.process_fonts_callback_async(callback, storage=storage)

    assert result.success is True
    assert result.fonts["css"]["clientUrl"] == "https://cdn.test/fonts/vms_fonts.css"
    css_on_disk = (tmp_path / "fonts-only" / "vms_fonts.css").read_text()
    assert "dummy-Inter-Regular.woff2" not in css_on_disk


# --------------------------------------------------------------------------- #
# Pre-save hooks                                                              #
# --------------------------------------------------------------------------- #

@respx.mock
def test_material_pre_save_allows_flow(
    config: VerstkaConfig, sign, build_content_zip
) -> None:
    content_url = "https://verstka.test/download/pre-ok"
    zip_bytes = build_content_zip(
        media={"x.png": b"b"}, vms_html="<img src=dummy-x.png>", vms_json={}
    ).read_bytes()
    respx.get(url__startswith=content_url).mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )
    storage = RecordingMediaStorage()

    seen: dict[str, Any] = {}

    def on_pre_save(ctx: ContentPreSaveContext) -> PreSaveDecision:
        seen["ctx"] = ctx
        return PreSaveDecision(allow=True)

    def on_finalize(_ctx: ContentFinalizeContext) -> ContentFinalizeResult:
        return ContentFinalizeResult(success=True)

    processor = CallbackProcessor(config)
    result = processor.process_material_callback_sync(
        _callback_payload(sign=sign, content_url=content_url, metadata={"site": "s1"}),
        storage=storage,
        on_finalize=on_finalize,
        on_pre_save=on_pre_save,
    )

    assert result.success is True
    assert seen["ctx"].material_id == "M1"
    assert seen["ctx"].metadata == {"site": "s1"}
    assert seen["ctx"].content_url == content_url
    assert storage.media_calls == [("x.png", "M1", {"site": "s1"})]


def test_material_pre_save_rejects_blocks_download_and_storage(
    config: VerstkaConfig, sign
) -> None:
    """Rejection must short-circuit before any HTTP/storage call."""
    # No respx mock installed on purpose — any outgoing request would raise.
    content_url = "https://verstka.test/download/blocked"
    storage = RecordingMediaStorage()

    def on_pre_save(ctx: ContentPreSaveContext) -> PreSaveDecision:
        if ctx.metadata.get("user_email") == "blocked@example.com":
            return PreSaveDecision(allow=False, reason="User blacklisted")
        return PreSaveDecision(allow=True)

    def on_finalize(_ctx):
        raise AssertionError("on_finalize must not run when pre_save rejects")

    processor = CallbackProcessor(config)
    result = processor.process_material_callback_sync(
        _callback_payload(
            sign=sign,
            content_url=content_url,
            metadata={"user_email": "blocked@example.com"},
        ),
        storage=storage,
        on_finalize=on_finalize,
        on_pre_save=on_pre_save,
    )

    assert result.success is False
    response = result.to_response()
    assert response["rc"] == 0
    assert response["rm"] == "User blacklisted"
    assert storage.media_calls == []


async def test_material_pre_save_async_rejects(config: VerstkaConfig, sign) -> None:
    content_url = "https://verstka.test/download/blocked-async"

    async def on_pre_save(_ctx: ContentPreSaveContext) -> PreSaveDecision:
        return PreSaveDecision(allow=False)

    async def on_finalize(_ctx):
        raise AssertionError("unreachable")

    processor = CallbackProcessor(config)
    result = await processor.process_material_callback_async(
        _callback_payload(sign=sign, content_url=content_url),
        storage=AsyncRecordingMediaStorage(),
        on_finalize=on_finalize,
        on_pre_save=on_pre_save,
    )

    assert result.success is False
    assert result.to_response()["rm"] == "Operation rejected"


def test_fonts_pre_save_rejects_blocks_download_and_storage(
    config: VerstkaConfig, sign, tmp_path: Path
) -> None:
    content_url = "https://verstka.test/fonts/blocked"
    storage = LocalFontStorage(tmp_path / "no-save")
    fonts_payload = {"list": [{"name": "Inter"}]}
    callback = {
        "material_id": "site-x",
        "content_url": content_url,
        "signature": sign("site-x", content_url),
        "metadata": {"fonts_callback_allowed": False},
        "fonts": fonts_payload,
    }

    def on_pre_save(ctx: FontsPreSaveContext) -> PreSaveDecision:
        assert ctx.fonts == fonts_payload
        assert ctx.content_url == content_url
        if not ctx.metadata.get("fonts_callback_allowed"):
            return PreSaveDecision(allow=False, reason="Fonts callback not enabled")
        return PreSaveDecision(allow=True)

    processor = CallbackProcessor(config)
    result = processor.process_fonts_callback_sync(
        callback,
        storage=storage,
        on_pre_save=on_pre_save,
    )

    assert result.success is False
    response = result.to_response()
    assert response["rc"] == 0
    assert response["rm"] == "Fonts callback not enabled"
    # The fonts payload is still returned so Verstka can log what was rejected.
    assert response["data"]["fonts"] == fonts_payload
    # Nothing should have been written to disk.
    target_dir = tmp_path / "no-save"
    assert list(target_dir.iterdir()) == []
