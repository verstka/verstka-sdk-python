"""Reference filesystem-backed implementations of the storage protocols.

Layout on disk::

    <root>/
        materials/<material_id>/<filename>   -- media files
        fonts/<filename>                     -- font binaries + manifests

Public URLs are built by joining ``base_url`` with the same relative path so
``LocalStorageAdapter`` works out of the box behind any static file server
(nginx ``root``, Django ``MEDIA_ROOT``, S3 with ``--website`` mode, etc.).
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class LocalStorageAdapter:
    """Synchronous local filesystem adapter.

    Implements the :class:`~verstka_sdk.storage.StorageAdapter` protocol.
    ``metadata`` is ignored (single-tenant deployment).
    """

    def __init__(
        self,
        root: str | Path,
        base_url: str,
        *,
        materials_subdir: str = "materials",
        fonts_subdir: str = "fonts",
    ) -> None:
        self.root = Path(root).resolve()
        self.base_url = base_url.rstrip("/")
        self.materials_subdir = materials_subdir.strip("/")
        self.fonts_subdir = fonts_subdir.strip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def save_media(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        del metadata
        target_dir = self.root / self.materials_subdir / material_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        shutil.copy2(temp_path, target)
        return f"{self.base_url}/{self.materials_subdir}/{material_id}/{filename}"

    def save_font_file(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        del material_id, metadata
        return self._save_to_fonts(filename, temp_path)

    def save_fonts_manifest(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        del material_id, metadata
        return self._save_to_fonts(filename, temp_path)

    def _save_to_fonts(self, filename: str, temp_path: Path) -> str:
        target_dir = self.root / self.fonts_subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        shutil.copy2(temp_path, target)
        return f"{self.base_url}/{self.fonts_subdir}/{filename}"


class LocalAsyncStorageAdapter:
    """Async local filesystem adapter.

    Delegates blocking IO to :func:`asyncio.to_thread` so the adapter stays
    non-blocking in async event loops without requiring third-party deps.
    """

    def __init__(
        self,
        root: str | Path,
        base_url: str,
        *,
        materials_subdir: str = "materials",
        fonts_subdir: str = "fonts",
    ) -> None:
        self._sync = LocalStorageAdapter(
            root,
            base_url,
            materials_subdir=materials_subdir,
            fonts_subdir=fonts_subdir,
        )

    @property
    def root(self) -> Path:
        return self._sync.root

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    async def save_media(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._sync.save_media, filename, temp_path, material_id, metadata
        )

    async def save_font_file(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._sync.save_font_file, filename, temp_path, material_id, metadata
        )

    async def save_fonts_manifest(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._sync.save_fonts_manifest, filename, temp_path, material_id, metadata
        )
