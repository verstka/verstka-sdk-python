"""Tests for ZIP parsing + download helpers."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from verstka_sdk.content import (
    cleanup_temp_dir,
    download_zip_async,
    download_zip_sync,
    extract_content_zip,
    extract_fonts_zip,
    make_content_temp_dir,
    make_fonts_temp_dir,
)
from verstka_sdk.exceptions import VerstkaApiError, VerstkaContentTooLargeError


def test_extract_content_zip_basic(build_content_zip, tmp_path: Path) -> None:
    zip_path = build_content_zip(
        media={"hero.png": b"pngdata"},
        vms_json={"assets": {"hero.png": {"clientUrl": "dummy-hero.png"}}},
        vms_html="<img src=dummy-hero.png>",
    )
    temp_dir = make_content_temp_dir()
    try:
        result = extract_content_zip(zip_path, temp_dir)
        assert set(result["media"].keys()) == {"hero.png"}
        assert Path(result["media"]["hero.png"]).read_bytes() == b"pngdata"
        assert result["vms_html"] == "<img src=dummy-hero.png>"
        assert json.loads(result["vms_json"]) == {
            "assets": {"hero.png": {"clientUrl": "dummy-hero.png"}}
        }
    finally:
        cleanup_temp_dir(temp_dir)


def test_extract_skips_unknown_extensions(build_content_zip) -> None:
    zip_path = build_content_zip(media={"evil.exe": b"bad", "ok.png": b"ok"})
    temp_dir = make_content_temp_dir()
    try:
        result = extract_content_zip(zip_path, temp_dir)
        assert set(result["media"].keys()) == {"ok.png"}
    finally:
        cleanup_temp_dir(temp_dir)


def test_extract_rejects_path_traversal(build_content_zip) -> None:
    zip_path = build_content_zip(
        extra_members={"../evil.txt": b"x", "/abs/evil.txt": b"y"},
    )
    temp_dir = make_content_temp_dir()
    try:
        result = extract_content_zip(zip_path, temp_dir)
        assert result["media"] == {}
        assert result["vms_json"] is None
        assert result["vms_html"] is None
    finally:
        cleanup_temp_dir(temp_dir)


def test_extract_handles_missing_vms_files(build_content_zip) -> None:
    zip_path = build_content_zip(media={"x.png": b"x"})
    temp_dir = make_content_temp_dir()
    try:
        result = extract_content_zip(zip_path, temp_dir)
        assert result["vms_json"] is None
        assert result["vms_html"] is None
        assert set(result["media"].keys()) == {"x.png"}
    finally:
        cleanup_temp_dir(temp_dir)


def test_extract_fonts_zip(build_fonts_zip) -> None:
    zip_path = build_fonts_zip(
        fonts={"Inter-Regular.woff2": b"font-bytes"},
        vms_fonts_json={"families": []},
        vms_fonts_css="@font-face { src: url(dummy-Inter-Regular.woff2); }",
    )
    temp_dir = make_fonts_temp_dir()
    try:
        result = extract_fonts_zip(zip_path, temp_dir)
        assert set(result["font_files"].keys()) == {"Inter-Regular.woff2"}
        assert Path(result["font_files"]["Inter-Regular.woff2"]).read_bytes() == b"font-bytes"
        assert result["vms_fonts_json_path"] is not None
        assert result["vms_fonts_css_path"] is not None
    finally:
        cleanup_temp_dir(temp_dir)


# --- download helpers -------------------------------------------------------


@respx.mock
def test_download_zip_sync_success(tmp_path: Path, build_content_zip) -> None:
    zip_bytes = build_content_zip(media={"x.png": b"hi"}).read_bytes()
    respx.get("https://x.test/content").mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )
    dest = tmp_path / "out.zip"
    download_zip_sync(
        "https://x.test/content",
        dest,
        max_size=10 * 1024,
        timeout=5.0,
    )
    assert dest.read_bytes() == zip_bytes


@respx.mock
def test_download_zip_sync_403(tmp_path: Path) -> None:
    respx.get("https://x.test/content").mock(return_value=httpx.Response(403))
    with pytest.raises(VerstkaApiError) as exc:
        download_zip_sync(
            "https://x.test/content",
            tmp_path / "out.zip",
            max_size=10 * 1024,
            timeout=5.0,
        )
    assert exc.value.status_code == 403


@respx.mock
def test_download_zip_sync_404(tmp_path: Path) -> None:
    respx.get("https://x.test/content").mock(return_value=httpx.Response(404))
    with pytest.raises(VerstkaApiError) as exc:
        download_zip_sync(
            "https://x.test/content",
            tmp_path / "out.zip",
            max_size=10 * 1024,
            timeout=5.0,
        )
    assert exc.value.status_code == 404


@respx.mock
def test_download_zip_sync_size_limit_via_content_length(tmp_path: Path) -> None:
    respx.get("https://x.test/content").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 2048,
            headers={"Content-Length": "2048"},
        )
    )
    with pytest.raises(VerstkaContentTooLargeError):
        download_zip_sync(
            "https://x.test/content",
            tmp_path / "out.zip",
            max_size=1024,
            timeout=5.0,
        )


@respx.mock
async def test_download_zip_async_success(tmp_path: Path, build_content_zip) -> None:
    zip_bytes = build_content_zip(media={"x.png": b"hi"}).read_bytes()
    respx.get("https://x.test/content").mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )
    dest = tmp_path / "out.zip"
    await download_zip_async(
        "https://x.test/content",
        dest,
        max_size=10 * 1024,
        timeout=5.0,
    )
    assert dest.read_bytes() == zip_bytes
