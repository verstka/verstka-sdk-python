"""Django REST Framework integration (optional extra ``drf``).

Requires Django + DRF (``pip install verstka-sdk[drf]``).
"""

from __future__ import annotations

from typing import Any

try:
    from rest_framework import views as drf_views
    from rest_framework.response import Response
    from rest_framework.views import APIView
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "djangorestframework is required for this integration. "
        "Install with: pip install 'verstka-sdk[drf]'"
    ) from exc

from ..callbacks import (
    ContentFinalizeFn,
    ContentPreSaveFn,
    FontsFinalizeFn,
    FontsPreSaveFn,
)
from ..client import VerstkaClient
from ..exceptions import VerstkaError
from ..storage import StorageAdapter
from ._base import map_exception


def _read_callback_signature(request: Any) -> str:
    """Extract ``X-Verstka-Signature`` from DRF/django request."""
    headers = getattr(request, "headers", None)
    if headers is not None:
        raw = headers.get("X-Verstka-Signature")
        if raw:
            return str(raw).strip()
    meta = getattr(request, "META", None)
    if isinstance(meta, dict):
        return str(meta.get("HTTP_X_VERSTKA_SIGNATURE") or "").strip()
    return ""


def build_callback_views(
    client: VerstkaClient,
    *,
    storage: StorageAdapter,
    on_content_finalize: ContentFinalizeFn,
    on_fonts_finalize: FontsFinalizeFn | None = None,
    on_content_pre_save: ContentPreSaveFn | None = None,
    on_fonts_pre_save: FontsPreSaveFn | None = None,
) -> dict[str, type]:
    """Return ``{"callback": APIView, "fonts_callback": APIView}``.

    Both views are always returned; ``on_fonts_finalize`` is optional when
    the SDK only needs to persist fonts through ``storage``.
    """

    class VerstkaCallbackAPIView(APIView):
        """DRF view that processes a material callback."""

        authentication_classes: list = []
        permission_classes: list = []

        def post(self, request: Any, *_args: Any, **_kwargs: Any) -> Any:
            payload = request.data or {}
            signature = _read_callback_signature(request)
            result = client.process_material_callback(
                payload,
                signature=signature,
                storage=storage,
                on_finalize=on_content_finalize,
                on_pre_save=on_content_pre_save,
            )
            return Response(result.to_response())

    class VerstkaFontsCallbackAPIView(APIView):
        """DRF view that processes a fonts callback."""

        authentication_classes: list = []
        permission_classes: list = []

        def post(self, request: Any, *_args: Any, **_kwargs: Any) -> Any:
            payload = request.data or {}
            signature = _read_callback_signature(request)
            result = client.process_fonts_callback(
                payload,
                signature=signature,
                storage=storage,
                on_finalize=on_fonts_finalize,
                on_pre_save=on_fonts_pre_save,
            )
            return Response(result.to_response())

    return {
        "callback": VerstkaCallbackAPIView,
        "fonts_callback": VerstkaFontsCallbackAPIView,
    }


def verstka_exception_handler(exc: BaseException, context: Any) -> Any:
    """DRF exception handler that maps ``VerstkaError`` to a ``Response``.

    Register in ``settings.py``::

        REST_FRAMEWORK = {
            "EXCEPTION_HANDLER": "verstka_sdk.integrations.drf.verstka_exception_handler",
        }
    """
    if isinstance(exc, VerstkaError):
        mapped = map_exception(exc)
        return Response(mapped.to_dict(), status=mapped.status)
    return drf_views.exception_handler(exc, context)
