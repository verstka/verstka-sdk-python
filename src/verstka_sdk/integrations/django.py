"""Django integration (optional extra ``django``).

Requires Django (``pip install verstka-sdk[django]``). Provides async
function-based views that wrap :class:`AsyncVerstkaClient` and an exception
middleware that converts ``VerstkaError`` into a ``JsonResponse``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from django.http import (
        HttpRequest,
        HttpResponse,
        HttpResponseNotAllowed,
        JsonResponse,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "django is required for this integration. "
        "Install with: pip install 'verstka-sdk[django]'"
    ) from exc

from ..async_client import AsyncVerstkaClient
from ..callbacks import (
    AsyncContentFinalizeFn,
    AsyncContentPreSaveFn,
    AsyncFontsFinalizeFn,
    AsyncFontsPreSaveFn,
)
from ..exceptions import VerstkaCallbackDataError, VerstkaError
from ..storage import AsyncStorageAdapter
from ._base import dispatch_callback_async, map_exception


def _read_callback_signature(request: HttpRequest) -> str:
    """Extract ``X-Verstka-Signature`` from Django (``headers`` or ``META``)."""
    headers = getattr(request, "headers", None)
    if headers is not None:
        raw = headers.get("X-Verstka-Signature")
        if raw:
            return str(raw).strip()
    return str(request.META.get("HTTP_X_VERSTKA_SIGNATURE") or "").strip()


def _load_payload(request: HttpRequest) -> dict[str, Any]:
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise VerstkaCallbackDataError("Expected application/json content type")
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        return json.loads(raw or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerstkaCallbackDataError(f"Invalid JSON payload: {exc}") from exc


def build_callback_views(
    client: AsyncVerstkaClient,
    *,
    storage: AsyncStorageAdapter,
    on_content_finalize: AsyncContentFinalizeFn,
    on_fonts_finalize: AsyncFontsFinalizeFn | None = None,
    on_content_pre_save: AsyncContentPreSaveFn | None = None,
    on_fonts_pre_save: AsyncFontsPreSaveFn | None = None,
) -> dict[str, Callable[[HttpRequest], Awaitable[HttpResponse]]]:
    """Return ``{"callback": view}`` (async views).

    The callback view handles both material callbacks and ``site_fonts_updated``
    font events. ``on_fonts_finalize`` is optional when the SDK only needs to
    persist fonts through ``storage``.
    """

    async def _material_view(request: HttpRequest) -> HttpResponse:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        try:
            payload = _load_payload(request)
            signature = _read_callback_signature(request)
            response_body = await dispatch_callback_async(
                client,
                payload,
                signature,
                storage=storage,
                on_content_finalize=on_content_finalize,
                on_fonts_finalize=on_fonts_finalize,
                on_content_pre_save=on_content_pre_save,
                on_fonts_pre_save=on_fonts_pre_save,
            )
        except VerstkaError as exc:
            mapped = map_exception(exc)
            return JsonResponse(mapped.to_dict(), status=mapped.status)
        return JsonResponse(response_body)

    return {"callback": _material_view}


class VerstkaExceptionMiddleware:
    """Django middleware that maps ``VerstkaError`` to ``JsonResponse``.

    Install in ``settings.MIDDLEWARE``::

        MIDDLEWARE = [
            ...
            "verstka_sdk.integrations.django.VerstkaExceptionMiddleware",
        ]
    """

    def __init__(self, get_response: Callable[[HttpRequest], Any]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> Any:
        return self.get_response(request)

    def process_exception(self, _request: HttpRequest, exc: BaseException) -> Any:
        if not isinstance(exc, VerstkaError):
            return None
        mapped = map_exception(exc)
        return JsonResponse(mapped.to_dict(), status=mapped.status)
