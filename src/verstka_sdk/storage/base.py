"""Storage Protocols used by Verstka callback processing.

All three methods share the same "context tail" ``(material_id, metadata)``.
The SDK never interprets ``metadata`` — it just forwards the callback payload
``metadata`` field to every save call so that multi-tenant adapters can pick
the right bucket/path/CDN based on ``site_id``, ``tenant``, ``environment``
or any other application-specific key.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageAdapter(Protocol):
    """Synchronous storage contract for :class:`~verstka_sdk.VerstkaClient`."""

    def save_media(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        """Persist one media file and return its public URL.

        Called once per file found in ``vms_media/`` during material callback
        processing.
        """

    def save_font_file(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        """Persist a single font binary (``.woff``/``.woff2``/``.ttf`` etc.)."""

    def save_fonts_manifest(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        """Persist ``vms_fonts.css`` or ``vms_fonts.json`` and return its URL."""


@runtime_checkable
class AsyncStorageAdapter(Protocol):
    """Async storage contract for :class:`~verstka_sdk.AsyncVerstkaClient`."""

    async def save_media(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...

    async def save_font_file(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...

    async def save_fonts_manifest(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...
