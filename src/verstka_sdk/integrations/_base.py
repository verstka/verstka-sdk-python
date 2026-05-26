"""Shared helpers for framework integrations: error mapping and response shape."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..exceptions import (
    VerstkaApiError,
    VerstkaCallbackDataError,
    VerstkaError,
    VerstkaMetadataJsonError,
    VerstkaSignatureError,
    VerstkaVmsJsonError,
)

FONTS_CALLBACK_EVENT = "site_fonts_updated"


@dataclass(frozen=True)
class ErrorResponse:
    """Normalized payload returned by every integration on ``VerstkaError``.

    All integrations emit JSON with ``error``, ``code``, ``message`` fields
    alongside the HTTP status code, so consumer code sees a consistent
    contract regardless of the host framework.
    """

    status: int
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "code": self.code, "message": self.message}


def map_exception(exc: BaseException) -> ErrorResponse:
    """Map a verstka-sdk exception to ``(status, code, message)``.

    Non-``VerstkaError`` exceptions bubble up as generic 500s; integrations
    may choose to re-raise them instead of handling them directly.
    """
    if isinstance(exc, VerstkaSignatureError):
        return ErrorResponse(400, "invalid_signature", exc.message)
    if isinstance(exc, VerstkaCallbackDataError):
        return ErrorResponse(400, "invalid_callback_data", exc.message)
    if isinstance(exc, VerstkaVmsJsonError):
        return ErrorResponse(400, "invalid_vms_json", exc.message)
    if isinstance(exc, VerstkaMetadataJsonError):
        return ErrorResponse(400, "invalid_metadata_json", exc.message)
    if isinstance(exc, VerstkaApiError):
        status = exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502
        return ErrorResponse(status, "verstka_api_error", exc.message)
    if isinstance(exc, VerstkaError):
        return ErrorResponse(500, "verstka_error", exc.message)
    return ErrorResponse(500, "internal_error", str(exc) or "Internal server error")


def is_fonts_callback_payload(payload: object) -> bool:
    """Return whether a shared callback payload is a site-fonts event."""
    if not isinstance(payload, Mapping):
        return False
    return payload.get("event") == FONTS_CALLBACK_EVENT


def require_extra(module_name: str, extra: str) -> Any:
    """Import ``module_name`` or raise a helpful ``ImportError``.

    Example::

        fastapi = require_extra("fastapi", "fastapi")
    """
    try:
        import importlib

        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"{module_name} is required for this integration. "
            f"Install with: pip install 'verstka-sdk[{extra}]'"
        ) from exc
