"""Callback processing core: signature check + pre_save + ZIP + storage + finalize.

Two entry points mirror the legacy ``process_callback_v2`` and
``process_site_fonts_callback`` service methods but stay framework-agnostic
and expose a clean storage-adapter contract:

- :meth:`CallbackProcessor.process_material_callback_sync` /
  :meth:`~CallbackProcessor.process_material_callback_async`
- :meth:`CallbackProcessor.process_fonts_callback_sync` /
  :meth:`~CallbackProcessor.process_fonts_callback_async`

Each entry point accepts a required ``signature`` argument (hex HMAC from the
HTTP ``X-Verstka-Signature`` header) plus two user hooks:

- ``on_pre_save`` (optional) — invoked right after signature verification and
  before the ZIP is downloaded. Returns a
  :class:`~verstka_sdk.finalize.PreSaveDecision`; a ``reject`` response
  short-circuits the flow and no storage writes occur.
- ``on_finalize`` — invoked after all storage writes succeed. For the
  material flow this hook is required (the SDK does not persist
  ``vms_json``/``vms_html`` itself); for the fonts flow it is optional, which
  is useful when the fonts storage URLs are known up front and no extra
  application state needs to be recorded.

Sync variants use :class:`~verstka_sdk.storage.StorageAdapter` and plain
callables; async variants use :class:`~verstka_sdk.storage.AsyncStorageAdapter`
and async callables. There is no transparent sync/async bridging — pick the
client that matches your framework.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .config import VerstkaConfig
from .content import (
    ExtractedContent,
    ExtractedFonts,
    cleanup_temp_dir,
    download_zip_async,
    download_zip_sync,
    extract_content_zip,
    extract_fonts_zip,
    make_content_temp_dir,
    make_fonts_temp_dir,
)
from .exceptions import VerstkaCallbackDataError, VerstkaSignatureError
from .finalize import (
    ContentFinalizeContext,
    ContentFinalizeResult,
    ContentPreSaveContext,
    FontsFinalizeContext,
    FontsFinalizeResult,
    FontsPreSaveContext,
    PreSaveDecision,
)
from .signatures import verify_signature
from .storage import AsyncStorageAdapter, StorageAdapter
from .urls import build_authorized_content_url

logger = logging.getLogger("verstka_sdk.callbacks")

CallbackData = Mapping[str, Any]

ContentFinalizeFn = Callable[[ContentFinalizeContext], ContentFinalizeResult]
AsyncContentFinalizeFn = Callable[[ContentFinalizeContext], Awaitable[ContentFinalizeResult]]
FontsFinalizeFn = Callable[[FontsFinalizeContext], FontsFinalizeResult]
AsyncFontsFinalizeFn = Callable[[FontsFinalizeContext], Awaitable[FontsFinalizeResult]]

ContentPreSaveFn = Callable[[ContentPreSaveContext], PreSaveDecision]
AsyncContentPreSaveFn = Callable[[ContentPreSaveContext], Awaitable[PreSaveDecision]]
FontsPreSaveFn = Callable[[FontsPreSaveContext], PreSaveDecision]
AsyncFontsPreSaveFn = Callable[[FontsPreSaveContext], Awaitable[PreSaveDecision]]

_REJECTED_DEFAULT_MESSAGE = "Operation rejected"

VMS_FONTS_CSS = "vms_fonts.css"
VMS_FONTS_JSON = "vms_fonts.json"


@dataclass
class MaterialCallbackResult:
    """Normalised outcome of ``process_material_callback``.

    :meth:`to_response` produces the dict body Verstka expects in the HTTP
    callback response: ``{"rc": 1|0, "rm": "...", "data": {...}}``.
    """

    success: bool
    message: str
    data: dict[str, Any]

    def to_response(self) -> dict[str, Any]:
        return {
            "rc": 1 if self.success else 0,
            "rm": self.message,
            "data": self.data,
        }


@dataclass
class FontsCallbackResult:
    """Normalised outcome of ``process_fonts_callback``."""

    success: bool
    message: str
    fonts: dict[str, Any]

    def to_response(self) -> dict[str, Any]:
        return {
            "rc": 1 if self.success else 0,
            "rm": self.message,
            "data": {"fonts": self.fonts},
        }


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #

def _verify_callback(
    data: CallbackData,
    signature: str,
    secret: str,
    *,
    debug: bool,
) -> None:
    """Verify HMAC using ``signature`` from the HTTP ``X-Verstka-Signature`` header only."""
    content_url = str(data.get("content_url") or "")
    material_id = str(data.get("material_id") or "")
    sig = str(signature or "")

    if not verify_signature(material_id, content_url, sig, secret):
        if debug:
            raise VerstkaSignatureError(
                f"Invalid signature {sig!r} for material_id={material_id!r}"
            )
        raise VerstkaSignatureError()


def _parse_vms_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse vms_json: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, Any], parsed)


def _apply_media_url_patches(
    *,
    filename: str,
    public_url: str,
    vms_html: str | None,
    vms_json_dict: dict[str, Any] | None,
) -> str | None:
    """Replace ``dummy-<filename>`` in html and set clientUrl in vms_json."""
    updated_html = vms_html
    if updated_html:
        dummy = f"dummy-{filename}"
        if dummy in updated_html:
            updated_html = updated_html.replace(dummy, public_url)

    if (
        vms_json_dict
        and isinstance(vms_json_dict.get("assets"), dict)
        and filename in vms_json_dict["assets"]
    ):
        asset = vms_json_dict["assets"][filename]
        if isinstance(asset, dict):
            asset["clientUrl"] = public_url

    return updated_html


def _patch_css_urls(css_text: str, saved_files: Mapping[str, str]) -> str:
    for file_id, public_url in saved_files.items():
        css_text = css_text.replace(f"dummy-{file_id}", public_url)
    return css_text


def _fill_font_client_urls(
    fonts: dict[str, Any],
    saved_files: Mapping[str, str],
    *,
    css_url: str | None,
) -> None:
    if isinstance(fonts.get("css"), dict) and css_url:
        fonts["css"]["clientUrl"] = css_url

    for family_entry in fonts.get("list") or []:
        if not isinstance(family_entry, dict):
            continue
        for variant in family_entry.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            files = variant.get("files") or {}
            if not isinstance(files, dict):
                continue
            for file_info in files.values():
                if not isinstance(file_info, dict):
                    continue
                file_id = file_info.get("id")
                if isinstance(file_id, str) and file_id in saved_files:
                    file_info["clientUrl"] = saved_files[file_id]


def _require_url(url: object, context: str) -> str:
    if not isinstance(url, str) or not url:
        raise VerstkaCallbackDataError(f"storage returned an invalid URL for {context}")
    return url


def _build_material_result(
    finalize_result: ContentFinalizeResult,
    *,
    callback_data: CallbackData,
    metadata: Mapping[str, Any],
    debug: bool,
) -> MaterialCallbackResult:
    data: dict[str, Any] = {}
    if finalize_result.vms_json is not None:
        data["vms_json"] = finalize_result.vms_json
    if debug:
        data["debug_info"] = {
            "callback_data": dict(callback_data),
            "metadata": dict(metadata),
        }
    return MaterialCallbackResult(
        success=bool(finalize_result.success),
        message="Saved successfully" if finalize_result.success else "Operation failed",
        data=data,
    )


def _build_material_rejection(
    decision: PreSaveDecision,
    *,
    callback_data: CallbackData,
    metadata: Mapping[str, Any],
    debug: bool,
) -> MaterialCallbackResult:
    data: dict[str, Any] = {}
    if debug:
        data["debug_info"] = {
            "callback_data": dict(callback_data),
            "metadata": dict(metadata),
            "rejected": True,
        }
    return MaterialCallbackResult(
        success=False,
        message=decision.reason or _REJECTED_DEFAULT_MESSAGE,
        data=data,
    )


def _build_fonts_result(
    finalize_result: FontsFinalizeResult,
    *,
    fallback_fonts: dict[str, Any],
) -> FontsCallbackResult:
    fonts = finalize_result.fonts if finalize_result.fonts is not None else fallback_fonts
    return FontsCallbackResult(
        success=bool(finalize_result.success),
        message="Fonts saved successfully" if finalize_result.success else "Operation failed",
        fonts=fonts,
    )


def _build_fonts_rejection(
    decision: PreSaveDecision,
    *,
    fallback_fonts: dict[str, Any],
) -> FontsCallbackResult:
    return FontsCallbackResult(
        success=False,
        message=decision.reason or _REJECTED_DEFAULT_MESSAGE,
        fonts=fallback_fonts,
    )


# --------------------------------------------------------------------------- #
# Processor                                                                   #
# --------------------------------------------------------------------------- #

class CallbackProcessor:
    """Shared engine used by :class:`VerstkaClient` / :class:`AsyncVerstkaClient`.

    Holds the active :class:`VerstkaConfig` only; all IO-bearing collaborators
    (storage, on_finalize, HTTP) are injected per call.
    """

    def __init__(self, config: VerstkaConfig) -> None:
        self.config = config

    # ----- sync: material ------------------------------------------------- #

    def process_material_callback_sync(
        self,
        callback_data: CallbackData,
        *,
        signature: str,
        storage: StorageAdapter,
        on_finalize: ContentFinalizeFn,
        on_pre_save: ContentPreSaveFn | None = None,
    ) -> MaterialCallbackResult:
        _verify_callback(
            callback_data,
            signature,
            self.config.api_secret,
            debug=self.config.debug,
        )

        material_id = str(callback_data.get("material_id") or "")
        if not material_id:
            raise VerstkaCallbackDataError("material_id is required")

        content_url = str(callback_data.get("content_url") or "")
        sig = str(signature or "")
        metadata = dict(callback_data.get("metadata") or {})

        if on_pre_save is not None:
            decision = on_pre_save(
                ContentPreSaveContext(
                    material_id=material_id,
                    metadata=metadata,
                    content_url=content_url,
                )
            )
            if not decision.allow:
                logger.info(
                    "material callback rejected by on_pre_save: material_id=%s reason=%s",
                    material_id,
                    decision.reason,
                )
                return _build_material_rejection(
                    decision,
                    callback_data=callback_data,
                    metadata=metadata,
                    debug=self.config.debug,
                )

        extracted: ExtractedContent | None = None
        try:
            if content_url:
                extracted = self._download_material_sync(content_url, material_id, sig)

            vms_json_dict = _parse_vms_json(extracted["vms_json"]) if extracted else None
            vms_html = extracted["vms_html"] if extracted else None
            media_files = extracted["media"] if extracted else {}

            saved_media_urls: dict[str, str] = {}
            for filename, temp_path in media_files.items():
                public_url = _require_url(
                    storage.save_media(filename, Path(temp_path), material_id, metadata),
                    f"media file {filename!r}",
                )
                saved_media_urls[filename] = public_url
                vms_html = _apply_media_url_patches(
                    filename=filename,
                    public_url=public_url,
                    vms_html=vms_html,
                    vms_json_dict=vms_json_dict,
                )

            ctx = ContentFinalizeContext(
                material_id=material_id,
                metadata=metadata,
                vms_json=vms_json_dict,
                vms_html=vms_html,
                saved_media_urls=saved_media_urls,
            )
            finalize_result = on_finalize(ctx)
        finally:
            if extracted is not None:
                cleanup_temp_dir(extracted["temp_dir"])

        return _build_material_result(
            finalize_result,
            callback_data=callback_data,
            metadata=metadata,
            debug=self.config.debug,
        )

    # ----- async: material ------------------------------------------------ #

    async def process_material_callback_async(
        self,
        callback_data: CallbackData,
        *,
        signature: str,
        storage: AsyncStorageAdapter,
        on_finalize: AsyncContentFinalizeFn,
        on_pre_save: AsyncContentPreSaveFn | None = None,
    ) -> MaterialCallbackResult:
        _verify_callback(
            callback_data,
            signature,
            self.config.api_secret,
            debug=self.config.debug,
        )

        material_id = str(callback_data.get("material_id") or "")
        if not material_id:
            raise VerstkaCallbackDataError("material_id is required")

        content_url = str(callback_data.get("content_url") or "")
        sig = str(signature or "")
        metadata = dict(callback_data.get("metadata") or {})

        if on_pre_save is not None:
            decision = await on_pre_save(
                ContentPreSaveContext(
                    material_id=material_id,
                    metadata=metadata,
                    content_url=content_url,
                )
            )
            if not decision.allow:
                logger.info(
                    "material callback rejected by on_pre_save: material_id=%s reason=%s",
                    material_id,
                    decision.reason,
                )
                return _build_material_rejection(
                    decision,
                    callback_data=callback_data,
                    metadata=metadata,
                    debug=self.config.debug,
                )

        extracted: ExtractedContent | None = None
        try:
            if content_url:
                extracted = await self._download_material_async(content_url, material_id, sig)

            vms_json_dict = _parse_vms_json(extracted["vms_json"]) if extracted else None
            vms_html = extracted["vms_html"] if extracted else None
            media_files = extracted["media"] if extracted else {}

            saved_media_urls: dict[str, str] = {}
            for filename, temp_path in media_files.items():
                public_url = _require_url(
                    await storage.save_media(
                        filename, Path(temp_path), material_id, metadata
                    ),
                    f"media file {filename!r}",
                )
                saved_media_urls[filename] = public_url
                vms_html = _apply_media_url_patches(
                    filename=filename,
                    public_url=public_url,
                    vms_html=vms_html,
                    vms_json_dict=vms_json_dict,
                )

            ctx = ContentFinalizeContext(
                material_id=material_id,
                metadata=metadata,
                vms_json=vms_json_dict,
                vms_html=vms_html,
                saved_media_urls=saved_media_urls,
            )
            finalize_result = await on_finalize(ctx)
        finally:
            if extracted is not None:
                cleanup_temp_dir(extracted["temp_dir"])

        return _build_material_result(
            finalize_result,
            callback_data=callback_data,
            metadata=metadata,
            debug=self.config.debug,
        )

    # ----- sync: fonts ---------------------------------------------------- #

    def process_fonts_callback_sync(
        self,
        callback_data: CallbackData,
        *,
        signature: str,
        storage: StorageAdapter,
        on_finalize: FontsFinalizeFn | None = None,
        on_pre_save: FontsPreSaveFn | None = None,
    ) -> FontsCallbackResult:
        _verify_callback(
            callback_data,
            signature,
            self.config.api_secret,
            debug=self.config.debug,
        )

        material_id = str(callback_data.get("material_id") or "")
        content_url = str(callback_data.get("content_url") or "")
        sig = str(signature or "")
        metadata = dict(callback_data.get("metadata") or {})
        fonts_payload = dict(callback_data.get("fonts") or {})

        if not content_url:
            raise VerstkaCallbackDataError("content_url is required for fonts callback")

        if on_pre_save is not None:
            decision = on_pre_save(
                FontsPreSaveContext(
                    material_id=material_id,
                    metadata=metadata,
                    content_url=content_url,
                    fonts=fonts_payload,
                )
            )
            if not decision.allow:
                logger.info(
                    "fonts callback rejected by on_pre_save: material_id=%s reason=%s",
                    material_id,
                    decision.reason,
                )
                return _build_fonts_rejection(decision, fallback_fonts=fonts_payload)

        extracted: ExtractedFonts | None = None
        try:
            extracted = self._download_fonts_sync(content_url, material_id, sig)

            saved_font_urls: dict[str, str] = {}
            for basename, temp_path in extracted["font_files"].items():
                url = _require_url(
                    storage.save_font_file(
                        basename, Path(temp_path), material_id, metadata
                    ),
                    f"font file {basename!r}",
                )
                saved_font_urls[basename] = url

            css_url = self._persist_css_sync(
                css_path=extracted["vms_fonts_css_path"],
                saved_font_urls=saved_font_urls,
                storage=storage,
                material_id=material_id,
                metadata=metadata,
            )
            json_url = self._persist_json_sync(
                json_path=extracted["vms_fonts_json_path"],
                storage=storage,
                material_id=material_id,
                metadata=metadata,
            )

            _fill_font_client_urls(fonts_payload, saved_font_urls, css_url=css_url)

            ctx = FontsFinalizeContext(
                material_id=material_id,
                metadata=metadata,
                fonts=fonts_payload,
                css_url=css_url,
                json_url=json_url,
                saved_font_urls=saved_font_urls,
            )
            finalize_result = (
                on_finalize(ctx) if on_finalize is not None else FontsFinalizeResult(success=True)
            )
        finally:
            if extracted is not None:
                cleanup_temp_dir(extracted["temp_dir"])

        return _build_fonts_result(finalize_result, fallback_fonts=fonts_payload)

    # ----- async: fonts --------------------------------------------------- #

    async def process_fonts_callback_async(
        self,
        callback_data: CallbackData,
        *,
        signature: str,
        storage: AsyncStorageAdapter,
        on_finalize: AsyncFontsFinalizeFn | None = None,
        on_pre_save: AsyncFontsPreSaveFn | None = None,
    ) -> FontsCallbackResult:
        _verify_callback(
            callback_data,
            signature,
            self.config.api_secret,
            debug=self.config.debug,
        )

        material_id = str(callback_data.get("material_id") or "")
        content_url = str(callback_data.get("content_url") or "")
        sig = str(signature or "")
        metadata = dict(callback_data.get("metadata") or {})
        fonts_payload = dict(callback_data.get("fonts") or {})

        if not content_url:
            raise VerstkaCallbackDataError("content_url is required for fonts callback")

        if on_pre_save is not None:
            decision = await on_pre_save(
                FontsPreSaveContext(
                    material_id=material_id,
                    metadata=metadata,
                    content_url=content_url,
                    fonts=fonts_payload,
                )
            )
            if not decision.allow:
                logger.info(
                    "fonts callback rejected by on_pre_save: material_id=%s reason=%s",
                    material_id,
                    decision.reason,
                )
                return _build_fonts_rejection(decision, fallback_fonts=fonts_payload)

        extracted: ExtractedFonts | None = None
        try:
            extracted = await self._download_fonts_async(content_url, material_id, sig)

            saved_font_urls: dict[str, str] = {}
            for basename, temp_path in extracted["font_files"].items():
                url = _require_url(
                    await storage.save_font_file(
                        basename, Path(temp_path), material_id, metadata
                    ),
                    f"font file {basename!r}",
                )
                saved_font_urls[basename] = url

            css_url = await self._persist_css_async(
                css_path=extracted["vms_fonts_css_path"],
                saved_font_urls=saved_font_urls,
                storage=storage,
                material_id=material_id,
                metadata=metadata,
            )
            json_url = await self._persist_json_async(
                json_path=extracted["vms_fonts_json_path"],
                storage=storage,
                material_id=material_id,
                metadata=metadata,
            )

            _fill_font_client_urls(fonts_payload, saved_font_urls, css_url=css_url)

            ctx = FontsFinalizeContext(
                material_id=material_id,
                metadata=metadata,
                fonts=fonts_payload,
                css_url=css_url,
                json_url=json_url,
                saved_font_urls=saved_font_urls,
            )
            finalize_result = (
                await on_finalize(ctx)
                if on_finalize is not None
                else FontsFinalizeResult(success=True)
            )
        finally:
            if extracted is not None:
                cleanup_temp_dir(extracted["temp_dir"])

        return _build_fonts_result(finalize_result, fallback_fonts=fonts_payload)

    # ----- fonts manifest helpers (sync) ---------------------------------- #

    def _persist_css_sync(
        self,
        *,
        css_path: str | None,
        saved_font_urls: Mapping[str, str],
        storage: StorageAdapter,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str | None:
        if not css_path or not os.path.exists(css_path):
            return None
        _rewrite_css_in_place(css_path, saved_font_urls)
        url = storage.save_fonts_manifest(
            VMS_FONTS_CSS, Path(css_path), material_id, metadata
        )
        return _require_url(url, f"manifest {VMS_FONTS_CSS!r}")

    def _persist_json_sync(
        self,
        *,
        json_path: str | None,
        storage: StorageAdapter,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str | None:
        if not json_path or not os.path.exists(json_path):
            return None
        url = storage.save_fonts_manifest(
            VMS_FONTS_JSON, Path(json_path), material_id, metadata
        )
        return _require_url(url, f"manifest {VMS_FONTS_JSON!r}")

    # ----- fonts manifest helpers (async) --------------------------------- #

    async def _persist_css_async(
        self,
        *,
        css_path: str | None,
        saved_font_urls: Mapping[str, str],
        storage: AsyncStorageAdapter,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str | None:
        if not css_path or not os.path.exists(css_path):
            return None
        _rewrite_css_in_place(css_path, saved_font_urls)
        url = await storage.save_fonts_manifest(
            VMS_FONTS_CSS, Path(css_path), material_id, metadata
        )
        return _require_url(url, f"manifest {VMS_FONTS_CSS!r}")

    async def _persist_json_async(
        self,
        *,
        json_path: str | None,
        storage: AsyncStorageAdapter,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str | None:
        if not json_path or not os.path.exists(json_path):
            return None
        url = await storage.save_fonts_manifest(
            VMS_FONTS_JSON, Path(json_path), material_id, metadata
        )
        return _require_url(url, f"manifest {VMS_FONTS_JSON!r}")

    # ----- download wrappers --------------------------------------------- #

    def _authorized_url(self, content_url: str, material_id: str) -> str:
        return build_authorized_content_url(content_url, self.config.api_key, material_id)

    def _download_material_sync(
        self, content_url: str, material_id: str, signature: str
    ) -> ExtractedContent:
        temp_dir = make_content_temp_dir()
        try:
            zip_path = os.path.join(temp_dir, "content.zip")
            download_zip_sync(
                self._authorized_url(content_url, material_id),
                zip_path,
                max_size=self.config.max_content_size,
                timeout=self.config.download_timeout,
                headers={"X-Verstka-Signature": signature},
            )
            return extract_content_zip(zip_path, temp_dir)
        except Exception:
            cleanup_temp_dir(temp_dir)
            raise

    async def _download_material_async(
        self, content_url: str, material_id: str, signature: str
    ) -> ExtractedContent:
        temp_dir = make_content_temp_dir()
        try:
            zip_path = os.path.join(temp_dir, "content.zip")
            await download_zip_async(
                self._authorized_url(content_url, material_id),
                zip_path,
                max_size=self.config.max_content_size,
                timeout=self.config.download_timeout,
                headers={"X-Verstka-Signature": signature},
            )
            return extract_content_zip(zip_path, temp_dir)
        except Exception:
            cleanup_temp_dir(temp_dir)
            raise

    def _download_fonts_sync(
        self, content_url: str, material_id: str, signature: str
    ) -> ExtractedFonts:
        temp_dir = make_fonts_temp_dir()
        try:
            zip_path = os.path.join(temp_dir, "fonts.zip")
            download_zip_sync(
                self._authorized_url(content_url, material_id),
                zip_path,
                max_size=self.config.max_content_size,
                timeout=self.config.download_timeout,
                headers={"X-Verstka-Signature": signature},
            )
            return extract_fonts_zip(zip_path, temp_dir)
        except Exception:
            cleanup_temp_dir(temp_dir)
            raise

    async def _download_fonts_async(
        self, content_url: str, material_id: str, signature: str
    ) -> ExtractedFonts:
        temp_dir = make_fonts_temp_dir()
        try:
            zip_path = os.path.join(temp_dir, "fonts.zip")
            await download_zip_async(
                self._authorized_url(content_url, material_id),
                zip_path,
                max_size=self.config.max_content_size,
                timeout=self.config.download_timeout,
                headers={"X-Verstka-Signature": signature},
            )
            return extract_fonts_zip(zip_path, temp_dir)
        except Exception:
            cleanup_temp_dir(temp_dir)
            raise


def _rewrite_css_in_place(css_path: str, saved_font_urls: Mapping[str, str]) -> None:
    """Replace ``dummy-<font_id>`` placeholders in CSS before persisting."""
    path = Path(css_path)
    css_text = path.read_text(encoding="utf-8")
    patched = _patch_css_urls(css_text, saved_font_urls)
    if patched != css_text:
        path.write_text(patched, encoding="utf-8")


__all__ = [
    "CallbackProcessor",
    "MaterialCallbackResult",
    "FontsCallbackResult",
    "ContentFinalizeFn",
    "AsyncContentFinalizeFn",
    "FontsFinalizeFn",
    "AsyncFontsFinalizeFn",
]
