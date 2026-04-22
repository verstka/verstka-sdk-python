"""Exception hierarchy for verstka-sdk.

All SDK-specific errors inherit from ``VerstkaError``. The integrations layer
maps these to HTTP responses; the core never raises framework-specific
exceptions.
"""

from __future__ import annotations


class VerstkaError(Exception):
    """Base class for all verstka-sdk exceptions."""

    default_message: str = "Verstka SDK error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class VerstkaSignatureError(VerstkaError):
    """Raised when callback HMAC signature is missing or invalid."""

    default_message = "Invalid callback signature"


class VerstkaCallbackDataError(VerstkaError):
    """Raised when callback payload is malformed (missing required fields)."""

    default_message = "Malformed callback data"


class VerstkaApiError(VerstkaError):
    """Raised on non-2xx response from Verstka API or content download."""

    default_message = "Verstka API error"

    def __init__(self, message: str | None = None, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class VerstkaContentTooLargeError(VerstkaApiError):
    """Raised when downloaded ZIP exceeds configured ``max_content_size``."""

    default_message = "Verstka content file is too large"


class VerstkaVmsJsonError(VerstkaError):
    """Raised when ``vms_json`` payload is not valid JSON."""

    default_message = "Invalid vms_json format"


class VerstkaMetadataJsonError(VerstkaError):
    """Raised when ``metadata_json`` payload is not valid JSON."""

    default_message = "Invalid metadata_json format"
