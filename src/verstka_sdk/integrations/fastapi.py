"""FastAPI integration (optional extra ``fastapi``).

Importing this module requires FastAPI to be installed (``pip install
verstka-sdk[fastapi]``). FastAPI is imported eagerly here so that route
definitions resolve type hints correctly.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any

try:
    from fastapi import APIRouter, FastAPI, Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "fastapi is required for this integration. "
        "Install with: pip install 'verstka-sdk[fastapi]'"
    ) from exc

from ..async_client import AsyncVerstkaClient
from ..callbacks import (
    AsyncContentFinalizeFn,
    AsyncContentPreSaveFn,
    AsyncFontsFinalizeFn,
    AsyncFontsPreSaveFn,
)
from ..exceptions import VerstkaError
from ..storage import AsyncStorageAdapter
from ._base import is_fonts_callback_payload, map_exception


def install_exception_handlers(app: FastAPI) -> None:
    """Register a JSON exception handler for all ``VerstkaError`` subclasses."""

    async def _handler(_request: Request, exc: VerstkaError) -> JSONResponse:
        mapped = map_exception(exc)
        return JSONResponse(status_code=mapped.status, content=mapped.to_dict())

    app.add_exception_handler(VerstkaError, _handler)  # type: ignore[arg-type]


def build_callback_router(
    client: AsyncVerstkaClient,
    *,
    storage: AsyncStorageAdapter,
    on_content_finalize: AsyncContentFinalizeFn,
    on_fonts_finalize: AsyncFontsFinalizeFn | None = None,
    on_content_pre_save: AsyncContentPreSaveFn | None = None,
    on_fonts_pre_save: AsyncFontsPreSaveFn | None = None,
    prefix: str = "/verstka",
    tags: Sequence[str | Enum] | None = None,
) -> APIRouter:
    """Return an ``APIRouter`` exposing ``/callback``.

    Font events (``event == "site_fonts_updated"``) are dispatched from this
    same callback URL into the fonts flow. ``on_fonts_finalize`` is optional:
    when omitted the SDK still persists fonts through ``storage`` and returns
    the default payload to Verstka, which is enough for static deployments
    where the font URLs are already known to the templates.

    ``on_*_pre_save`` hooks receive ``material_id``/``metadata`` *before* any
    ZIP download and can short-circuit the flow with
    :class:`~verstka_sdk.finalize.PreSaveDecision` (e.g. to enforce
    per-tenant/per-user access control).

    Callback HMAC must be sent in the ``X-Verstka-Signature`` HTTP header (not
    in the JSON body).
    """
    router = APIRouter(prefix=prefix, tags=list(tags) if tags else ["verstka"])

    @router.post("/callback")
    async def _material_callback(request: Request) -> dict[str, Any]:
        payload: dict[str, Any] = await request.json()
        signature = (request.headers.get("X-Verstka-Signature") or "").strip()
        if is_fonts_callback_payload(payload):
            fonts_result = await client.process_fonts_callback(
                payload,
                signature=signature,
                storage=storage,
                on_finalize=on_fonts_finalize,
                on_pre_save=on_fonts_pre_save,
            )
            return fonts_result.to_response()

        material_result = await client.process_material_callback(
            payload,
            signature=signature,
            storage=storage,
            on_finalize=on_content_finalize,
            on_pre_save=on_content_pre_save,
        )
        return material_result.to_response()

    return router
