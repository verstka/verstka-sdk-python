"""Asynchronous Verstka client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .callbacks import (
    AsyncContentFinalizeFn,
    AsyncContentPreSaveFn,
    AsyncFontsFinalizeFn,
    AsyncFontsPreSaveFn,
    CallbackProcessor,
    FontsCallbackResult,
    MaterialCallbackResult,
)
from .session import build_session_payload, parse_editor_response
from .config import VerstkaConfig
from .storage import AsyncStorageAdapter


class AsyncVerstkaClient:
    """Async client for Verstka API v2 (FastAPI/Starlette/Django async)."""

    def __init__(
        self,
        config: VerstkaConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=config.request_timeout)
        self._processor = CallbackProcessor(config, async_http_client=self._http_client)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> AsyncVerstkaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get_editor_url(
        self,
        material_id: str,
        *,
        vms_json: str | Mapping[str, Any] | None = None,
        metadata: str | Mapping[str, Any] | None = None,
    ) -> str:
        payload, signature = build_session_payload(self.config, material_id, vms_json, metadata)
        response = await self._http_client.post(
            self.config.session_open_url,
            json=payload,
            headers={"X-Verstka-Signature": signature},
            timeout=self.config.request_timeout,
        )
        return parse_editor_response(response)

    async def process_material_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        signature: str,
        storage: AsyncStorageAdapter,
        on_finalize: AsyncContentFinalizeFn,
        on_pre_save: AsyncContentPreSaveFn | None = None,
    ) -> MaterialCallbackResult:
        """Async counterpart of :meth:`VerstkaClient.process_material_callback`."""
        return await self._processor.process_material_callback_async(
            callback_data,
            signature=signature,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )

    async def process_fonts_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        signature: str,
        storage: AsyncStorageAdapter,
        on_finalize: AsyncFontsFinalizeFn | None = None,
        on_pre_save: AsyncFontsPreSaveFn | None = None,
    ) -> FontsCallbackResult:
        """Async counterpart of :meth:`VerstkaClient.process_fonts_callback`."""
        return await self._processor.process_fonts_callback_async(
            callback_data,
            signature=signature,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )
