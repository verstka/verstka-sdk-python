"""Synchronous Verstka client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .callbacks import (
    CallbackProcessor,
    ContentFinalizeFn,
    ContentPreSaveFn,
    FontsCallbackResult,
    FontsFinalizeFn,
    FontsPreSaveFn,
    MaterialCallbackResult,
)
from .config import VerstkaConfig
from .session import build_session_payload, parse_editor_response
from .storage import StorageAdapter


class VerstkaClient:
    """Sync client for Verstka API v2.

    Use this from sync frameworks (Flask, plain WSGI) or scripts. For async
    frameworks (FastAPI, Starlette) prefer :class:`AsyncVerstkaClient`.
    """

    def __init__(
        self,
        config: VerstkaConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(timeout=config.request_timeout)
        self._processor = CallbackProcessor(config, sync_http_client=self._http_client)

    # ----- lifecycle ---------------------------------------------------- #

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> VerstkaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- public API --------------------------------------------------- #

    def get_editor_url(
        self,
        material_id: str,
        *,
        vms_json: str | Mapping[str, Any] | None = None,
        metadata: str | Mapping[str, Any] | None = None,
    ) -> str:
        """POST ``session/open`` and return the Verstka editor URL."""
        payload, signature = build_session_payload(self.config, material_id, vms_json, metadata)
        response = self._http_client.post(
            self.config.session_open_url,
            json=payload,
            headers={"X-Verstka-Signature": signature},
            timeout=self.config.request_timeout,
        )
        return parse_editor_response(response)

    def process_material_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        signature: str,
        storage: StorageAdapter,
        on_finalize: ContentFinalizeFn,
        on_pre_save: ContentPreSaveFn | None = None,
    ) -> MaterialCallbackResult:
        """Verify, download, extract, save media and hand off to ``on_finalize``.

        Pass ``signature`` from the incoming HTTP ``X-Verstka-Signature`` header
        (same hex HMAC as for ``session/open``).

        The SDK persists each media file via ``storage.save_media`` and
        rewrites every ``dummy-<filename>`` placeholder in ``vms_html`` and
        ``vms_json.assets[*].clientUrl`` before invoking ``on_finalize``.

        When ``on_pre_save`` is provided it runs after signature verification
        and before the ZIP download; returning
        :class:`~verstka_sdk.finalize.PreSaveDecision` with ``allow=False``
        short-circuits the flow and the HTTP response will report ``rc=0``.
        """
        return self._processor.process_material_callback_sync(
            callback_data,
            signature=signature,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )

    def process_fonts_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        signature: str,
        storage: StorageAdapter,
        on_finalize: FontsFinalizeFn | None = None,
        on_pre_save: FontsPreSaveFn | None = None,
    ) -> FontsCallbackResult:
        """Verify, download, extract, save fonts and hand off to ``on_finalize``.

        Pass ``signature`` from the incoming HTTP ``X-Verstka-Signature`` header.

        Each font binary is saved via ``storage.save_font_file``; ``vms_fonts.css``
        and ``vms_fonts.json`` are saved via ``storage.save_fonts_manifest``.
        ``dummy-<font_id>`` placeholders inside CSS are rewritten in memory
        before ``save_fonts_manifest`` is called, so no secondary write to the
        remote storage is necessary.

        ``on_finalize`` is optional: when omitted the SDK still persists every
        font binary and manifest via ``storage`` and returns the default
        payload that matches the Verstka contract. Pass an ``on_finalize``
        when the application needs to record the saved URLs, recompute caches,
        etc. ``on_pre_save`` mirrors the material-callback hook and can reject
        the whole operation before any ZIP is downloaded.
        """
        return self._processor.process_fonts_callback_sync(
            callback_data,
            signature=signature,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )
