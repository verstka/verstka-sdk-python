"""Storage adapters for verstka-sdk.

Two protocols (:class:`StorageAdapter` for sync clients,
:class:`AsyncStorageAdapter` for async clients) describe how the SDK persists
media files, font binaries and fonts manifests. Each method receives a tail of
``(material_id, metadata)`` so the adapter can make tenant/site-level routing
decisions on its own (e.g. pick an S3 bucket based on ``metadata["site_id"]``).

Reference implementations are shipped:
- :class:`LocalStorageAdapter` — stores everything on a local filesystem.
- :class:`LocalAsyncStorageAdapter` — async twin that offloads IO to a thread.
"""

from .base import AsyncStorageAdapter, StorageAdapter
from .local import LocalAsyncStorageAdapter, LocalStorageAdapter

__all__ = [
    "AsyncStorageAdapter",
    "LocalAsyncStorageAdapter",
    "LocalStorageAdapter",
    "StorageAdapter",
]
