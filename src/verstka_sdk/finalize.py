"""Typed context passed to user ``on_pre_save`` / ``on_finalize`` callbacks.

These dataclasses are *not* pydantic models — they are internal envelopes
the SDK constructs during callback processing and then hands to user code.
No validation is needed (all data has already been sanitised) and no JSON
serialisation is expected; dataclasses are cheaper and more idiomatic here.

Two families of callbacks are wired into the processor:

- ``on_*_pre_save`` receives a ``*PreSaveContext`` *before* any storage IO
  begins and returns a :class:`PreSaveDecision` that either allows or rejects
  the whole operation (e.g. to enforce blacklists on ``metadata``).
- ``on_*_finalize`` receives a ``*FinalizeContext`` *after* all storage IO
  has happened and decides what to return to the Verstka editor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

_EMPTY_MAP: Mapping[str, Any] = MappingProxyType({})


@dataclass
class PreSaveDecision:
    """User response to a ``*PreSaveContext``.

    ``allow=True`` lets the processor continue downloading/saving the
    callback payload. ``allow=False`` short-circuits the flow: no ZIP is
    downloaded, no :class:`~verstka_sdk.storage.StorageAdapter` method is
    called, and the HTTP response body reports ``rc=0`` with ``reason``
    (or a default ``"Operation rejected"``) as ``rm``.
    """

    allow: bool
    reason: str | None = None


@dataclass(frozen=True)
class ContentPreSaveContext:
    """Data handed to ``on_pre_save`` before a material callback starts.

    Exposes only the fields available *before* the ZIP is downloaded so
    decisions can be made cheaply (e.g. tenant/user blacklist lookups on
    ``metadata``). The ``content_url`` is provided for advanced use cases
    like rate limiting per-URL.
    """

    material_id: str
    metadata: Mapping[str, Any]
    content_url: str


@dataclass(frozen=True)
class FontsPreSaveContext:
    """Data handed to ``on_pre_save`` before a fonts callback starts.

    The ``fonts`` tree from the callback payload is included so callers can
    make decisions based on the set of declared fonts (e.g. allowlists of
    family ids) without downloading the ZIP first.
    """

    material_id: str
    metadata: Mapping[str, Any]
    content_url: str
    fonts: dict[str, Any]


@dataclass(frozen=True)
class ContentFinalizeContext:
    """Data handed to ``on_finalize`` during material callback processing.

    All ``dummy-*`` placeholders have already been rewritten in ``vms_html``
    and ``vms_json.assets[*].clientUrl`` by the SDK.
    """

    material_id: str
    metadata: Mapping[str, Any]
    vms_json: dict[str, Any] | None
    vms_html: str | None
    saved_media_urls: Mapping[str, str] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass
class ContentFinalizeResult:
    """User response to :class:`ContentFinalizeContext`.

    If ``vms_json`` is non-``None`` the SDK includes it in the HTTP response
    body under ``data.vms_json`` so the Verstka editor can receive an updated
    state (e.g. with server-assigned asset IDs).
    """

    success: bool
    vms_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class FontsFinalizeContext:
    """Data handed to ``on_finalize`` during fonts callback processing."""

    material_id: str
    metadata: Mapping[str, Any]
    fonts: dict[str, Any]
    css_url: str | None
    json_url: str | None
    saved_font_urls: Mapping[str, str] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass
class FontsFinalizeResult:
    """User response to :class:`FontsFinalizeContext`.

    If ``fonts`` is non-``None`` it replaces the default ``fonts`` tree that
    the SDK builds from the payload in the HTTP response body.
    """

    success: bool
    fonts: dict[str, Any] | None = None
