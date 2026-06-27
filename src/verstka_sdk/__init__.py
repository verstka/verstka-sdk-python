"""verstka-sdk: Python SDK for Verstka API v2."""

from __future__ import annotations

from .async_client import AsyncVerstkaClient
from .callbacks import (
    AsyncContentFinalizeFn,
    AsyncContentPreSaveFn,
    AsyncFontsFinalizeFn,
    AsyncFontsPreSaveFn,
    CallbackProcessor,
    ContentFinalizeFn,
    ContentPreSaveFn,
    FontsCallbackResult,
    FontsFinalizeFn,
    FontsPreSaveFn,
    MaterialCallbackResult,
)
from .client import VerstkaClient
from .config import DEFAULT_API_URL, DEFAULT_MAX_CONTENT_SIZE, VerstkaConfig
from .exceptions import (
    VerstkaApiError,
    VerstkaCallbackDataError,
    VerstkaContentTooLargeError,
    VerstkaError,
    VerstkaMetadataJsonError,
    VerstkaSignatureError,
    VerstkaVmsJsonError,
)
from .finalize import (
    ContentFinalizeContext,
    ContentFinalizeResult,
    ContentPreSaveContext,
    FontsFinalizeContext,
    FontsFinalizeResult,
    FontsPreSaveContext,
    PreSaveDecision,
)
from .signatures import sign_material, verify_signature
from .storage import (
    AsyncStorageAdapter,
    LocalAsyncStorageAdapter,
    LocalStorageAdapter,
    StorageAdapter,
)
from .urls import build_authorized_content_url

try:
    from importlib.metadata import version as _package_version

    __version__ = _package_version("verstka-sdk")
except Exception:  # pragma: no cover - editable installs without metadata
    __version__ = "0.1.7"

__all__ = [
    # Clients
    "AsyncVerstkaClient",
    "VerstkaClient",
    "VerstkaConfig",
    "DEFAULT_API_URL",
    "DEFAULT_MAX_CONTENT_SIZE",
    # Exceptions
    "VerstkaError",
    "VerstkaApiError",
    "VerstkaCallbackDataError",
    "VerstkaContentTooLargeError",
    "VerstkaSignatureError",
    "VerstkaVmsJsonError",
    "VerstkaMetadataJsonError",
    # Callback processing
    "CallbackProcessor",
    "MaterialCallbackResult",
    "FontsCallbackResult",
    "ContentFinalizeFn",
    "AsyncContentFinalizeFn",
    "FontsFinalizeFn",
    "AsyncFontsFinalizeFn",
    "ContentPreSaveFn",
    "AsyncContentPreSaveFn",
    "FontsPreSaveFn",
    "AsyncFontsPreSaveFn",
    # Finalize / pre-save context / result
    "ContentFinalizeContext",
    "ContentFinalizeResult",
    "FontsFinalizeContext",
    "FontsFinalizeResult",
    "ContentPreSaveContext",
    "FontsPreSaveContext",
    "PreSaveDecision",
    # Storage
    "StorageAdapter",
    "AsyncStorageAdapter",
    "LocalStorageAdapter",
    "LocalAsyncStorageAdapter",
    # Low-level helpers
    "sign_material",
    "verify_signature",
    "build_authorized_content_url",
    # Metadata
    "__version__",
]
