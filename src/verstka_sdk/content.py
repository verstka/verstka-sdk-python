"""ZIP download + extraction for Verstka content callbacks.

The Verstka content ZIP is structured as:

- ``vms_media/<file>`` — media assets (images, video, audio, documents,
  Lottie animations).
- ``vms_json.json`` — VMS editor state as JSON.
- ``vms_html.html`` — rendered HTML.

Fonts ZIPs have a different layout:

- ``vms_fonts/<file>`` — font files.
- ``vms_fonts.json`` / ``vms_fonts.css`` — metadata.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypedDict

import httpx

from .exceptions import VerstkaApiError, VerstkaContentTooLargeError

logger = logging.getLogger("verstka_sdk.content")

CONTENT_TEMP_PREFIX = "verstka_content_"
FONTS_TEMP_PREFIX = "verstka_fonts_"
CHUNK_SIZE = 8192

MEDIA_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".avif",
    # Videos
    ".mp4", ".webm", ".ogv",
    # Audio
    ".mp3", ".wav", ".ogg", ".aac", ".m4a",
    # Documents
    ".pdf", ".txt",
    # Lottie / JSON animations (vms_media contains editor assets only).
    ".json", ".lottie",
})

FONT_EXTENSIONS: frozenset[str] = frozenset({
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})


class ExtractedContent(TypedDict):
    """Result of ``extract_content_zip``."""

    media: dict[str, str]
    vms_json: str | None
    vms_html: str | None
    temp_dir: str


class ExtractedFonts(TypedDict):
    """Result of ``extract_fonts_zip``."""

    font_files: dict[str, str]  # basename -> absolute path inside temp dir
    vms_fonts_json_path: str | None
    vms_fonts_css_path: str | None
    temp_dir: str


def _is_safe_member(name: str) -> bool:
    """Guard against path traversal / absolute paths inside ZIPs."""
    return ".." not in name and not name.startswith("/")


def _check_size(current: int, limit: int) -> None:
    if current > limit:
        raise VerstkaContentTooLargeError(
            f"Content file too large: {current} bytes (max: {limit})"
        )


def _status_to_error(status: int) -> VerstkaApiError | None:
    if status == 403:
        return VerstkaApiError("Access denied: invalid API key or signature", status_code=403)
    if status == 404:
        return VerstkaApiError(
            "Content not found: invalid material_id or expired content",
            status_code=404,
        )
    if status == 500:
        return VerstkaApiError("Server error: content service unavailable", status_code=500)
    return None


# --------------------------------------------------------------------------- #
# Download helpers
# --------------------------------------------------------------------------- #

def download_zip_sync(
    url: str,
    dest_path: str | os.PathLike[str],
    *,
    max_size: int,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    http_client: httpx.Client | None = None,
) -> None:
    """Stream ``url`` into ``dest_path`` using ``httpx.Client``.

    When ``http_client`` is provided it is reused (not closed by this helper).
    Raises ``VerstkaApiError`` on HTTP errors or network failures.
    """
    try:
        if http_client is not None:
            with http_client.stream(
                "GET", url, headers=dict(headers or {}), timeout=timeout
            ) as response:
                _process_sync_download_response(response, dest_path, max_size)
            return

        with httpx.Client(timeout=timeout) as client, client.stream(
            "GET", url, headers=dict(headers or {})
        ) as response:
            _process_sync_download_response(response, dest_path, max_size)
    except httpx.HTTPError as exc:
        raise VerstkaApiError(f"Failed to download content: {exc}") from exc


async def download_zip_async(
    url: str,
    dest_path: str | os.PathLike[str],
    *,
    max_size: int,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Stream ``url`` into ``dest_path`` using ``httpx.AsyncClient``."""
    try:
        if http_client is not None:
            async with http_client.stream(
                "GET", url, headers=dict(headers or {}), timeout=timeout
            ) as response:
                await _process_async_download_response(response, dest_path, max_size)
            return

        async with httpx.AsyncClient(timeout=timeout) as client, client.stream(
            "GET", url, headers=dict(headers or {})
        ) as response:
            await _process_async_download_response(response, dest_path, max_size)
    except httpx.HTTPError as exc:
        raise VerstkaApiError(f"Failed to download content: {exc}") from exc


def _process_sync_download_response(
    response: httpx.Response,
    dest_path: str | os.PathLike[str],
    max_size: int,
) -> None:
    mapped = _status_to_error(response.status_code)
    if mapped is not None:
        raise mapped
    response.raise_for_status()
    _enforce_content_length(response, max_size)
    _write_stream(response.iter_bytes(CHUNK_SIZE), dest_path, max_size)


async def _process_async_download_response(
    response: httpx.Response,
    dest_path: str | os.PathLike[str],
    max_size: int,
) -> None:
    mapped = _status_to_error(response.status_code)
    if mapped is not None:
        raise mapped
    response.raise_for_status()
    _enforce_content_length(response, max_size)

    downloaded = 0
    with open(dest_path, "wb") as fh:
        async for chunk in response.aiter_bytes(CHUNK_SIZE):
            if not chunk:
                continue
            fh.write(chunk)
            downloaded += len(chunk)
            _check_size(downloaded, max_size)


def _enforce_content_length(response: httpx.Response, max_size: int) -> None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        size = int(content_length)
    except ValueError:
        return
    _check_size(size, max_size)


def _write_stream(chunks: Iterable[bytes], dest_path: str | os.PathLike[str], max_size: int) -> None:
    downloaded = 0
    with open(dest_path, "wb") as fh:
        for chunk in chunks:
            if not chunk:
                continue
            fh.write(chunk)
            downloaded += len(chunk)
            _check_size(downloaded, max_size)


# --------------------------------------------------------------------------- #
# Archive parsing
# --------------------------------------------------------------------------- #

def extract_content_zip(zip_path: str | os.PathLike[str], temp_dir: str) -> ExtractedContent:
    """Parse a ``material`` content ZIP and extract media files to ``temp_dir``."""
    media_files: dict[str, str] = {}
    vms_json_content: str | None = None
    vms_html_content: str | None = None

    temp_dir_abs = os.path.abspath(temp_dir)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for info in zip_ref.infolist():
            if info.is_dir():
                continue
            if not _is_safe_member(info.filename):
                continue

            normalized = info.filename.replace("\\", "/")

            if normalized == "vms_json.json":
                try:
                    with zip_ref.open(info) as fh:
                        vms_json_content = fh.read().decode("utf-8")
                except (UnicodeDecodeError, zipfile.BadZipFile) as exc:
                    logger.warning("Failed to read vms_json.json: %s", exc)
                continue

            if normalized == "vms_html.html":
                try:
                    with zip_ref.open(info) as fh:
                        vms_html_content = fh.read().decode("utf-8")
                except (UnicodeDecodeError, zipfile.BadZipFile) as exc:
                    logger.warning("Failed to read vms_html.html: %s", exc)
                continue

            if normalized.startswith("vms_media/"):
                filename = os.path.basename(normalized)
                if not filename:
                    continue
                ext = Path(filename).suffix.lower()
                if ext not in MEDIA_EXTENSIONS:
                    continue
                try:
                    extracted_path = zip_ref.extract(info, temp_dir)
                    absolute_path = os.path.abspath(extracted_path)
                    if not absolute_path.startswith(temp_dir_abs):
                        # Safety net against symlinked traversal.
                        os.remove(absolute_path)
                        continue
                    media_files[filename] = absolute_path
                except (zipfile.BadZipFile, OSError) as exc:
                    logger.warning("Failed to extract %s: %s", filename, exc)
                    continue

    return ExtractedContent(
        media=media_files,
        vms_json=vms_json_content,
        vms_html=vms_html_content,
        temp_dir=temp_dir_abs,
    )


def extract_fonts_zip(zip_path: str | os.PathLike[str], temp_dir: str) -> ExtractedFonts:
    """Parse a ``site_fonts`` ZIP and extract font files + metadata to ``temp_dir``."""
    font_files: dict[str, str] = {}
    vms_fonts_json_path: str | None = None
    vms_fonts_css_path: str | None = None

    temp_dir_abs = os.path.abspath(temp_dir)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for info in zip_ref.infolist():
            if info.is_dir():
                continue
            if not _is_safe_member(info.filename):
                continue

            normalized = info.filename.replace("\\", "/")
            basename = os.path.basename(normalized)
            if not basename:
                continue

            if normalized == "vms_fonts.json":
                extracted = os.path.abspath(zip_ref.extract(info, temp_dir))
                vms_fonts_json_path = extracted
                continue

            if normalized == "vms_fonts.css":
                extracted = os.path.abspath(zip_ref.extract(info, temp_dir))
                vms_fonts_css_path = extracted
                continue

            if normalized.startswith("vms_fonts/"):
                ext = Path(basename).suffix.lower()
                if ext not in FONT_EXTENSIONS:
                    continue
                target = os.path.join(temp_dir, "vms_fonts", basename)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zip_ref.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                absolute_path = os.path.abspath(target)
                if not absolute_path.startswith(temp_dir_abs):
                    os.remove(absolute_path)
                    continue
                font_files[basename] = absolute_path

    return ExtractedFonts(
        font_files=font_files,
        vms_fonts_json_path=vms_fonts_json_path,
        vms_fonts_css_path=vms_fonts_css_path,
        temp_dir=temp_dir_abs,
    )


# --------------------------------------------------------------------------- #
# High-level combined helpers
# --------------------------------------------------------------------------- #

def make_content_temp_dir() -> str:
    return tempfile.mkdtemp(prefix=CONTENT_TEMP_PREFIX)


def make_fonts_temp_dir() -> str:
    return tempfile.mkdtemp(prefix=FONTS_TEMP_PREFIX)


def cleanup_temp_dir(temp_dir: str | None) -> None:
    if not temp_dir:
        return
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except OSError:  # pragma: no cover - defensive
        logger.exception("Failed to clean up temp dir %s", temp_dir)
