"""Synchronous Verstka client."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from .callbacks import (
    CallbackProcessor,
    ContentFinalizeFn,
    ContentPreSaveFn,
    FontsCallbackResult,
    FontsFinalizeFn,
    FontsPreSaveFn,
    MaterialCallbackResult,
)
from .config import VerstkaConfig
from .exceptions import VerstkaApiError, VerstkaMetadataJsonError, VerstkaVmsJsonError
from .signatures import sign_material
from .storage import StorageAdapter


class VerstkaClient:
    """Sync client for Verstka API v2.

    Use this from sync frameworks (Flask, plain WSGI) or scripts. For async
    frameworks (FastAPI, Starlette) prefer :class:`AsyncVerstkaClient`.
    """

    def __init__(
        self,
        config: VerstkaConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._processor = CallbackProcessor(config)
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(timeout=config.request_timeout)

    # ----- lifecycle ---------------------------------------------------- #

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> VerstkaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- public API --------------------------------------------------- #

    def get_editor_url(
        self,
        material_id: str,
        *,
        vms_json: str | Mapping[str, Any] | None = None,
        metadata: str | Mapping[str, Any] | None = None,
    ) -> str:
        """POST ``session/open`` and return the Verstka editor URL."""
        payload, signature = _build_session_payload(self.config, material_id, vms_json, metadata)
        response = self._http_client.post(
            self.config.session_open_url,
            json=payload,
            headers={"X-Verstka-Signature": signature},
            timeout=self.config.request_timeout,
        )
        return _parse_editor_response(response)

    def process_material_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        storage: StorageAdapter,
        on_finalize: ContentFinalizeFn,
        on_pre_save: ContentPreSaveFn | None = None,
    ) -> MaterialCallbackResult:
        """Verify, download, extract, save media and hand off to ``on_finalize``.

        The SDK persists each media file via ``storage.save_media`` and
        rewrites every ``dummy-<filename>`` placeholder in ``vms_html`` and
        ``vms_json.assets[*].clientUrl`` before invoking ``on_finalize``.

        When ``on_pre_save`` is provided it runs after signature verification
        and before the ZIP download; returning
        :class:`~verstka_sdk.finalize.PreSaveDecision` with ``allow=False``
        short-circuits the flow and the HTTP response will report ``rc=0``.
        """
        return self._processor.process_material_callback_sync(
            callback_data,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )

    def process_fonts_callback(
        self,
        callback_data: Mapping[str, Any],
        *,
        storage: StorageAdapter,
        on_finalize: FontsFinalizeFn | None = None,
        on_pre_save: FontsPreSaveFn | None = None,
    ) -> FontsCallbackResult:
        """Verify, download, extract, save fonts and hand off to ``on_finalize``.

        Each font binary is saved via ``storage.save_font_file``; ``vms_fonts.css``
        and ``vms_fonts.json`` are saved via ``storage.save_fonts_manifest``.
        ``dummy-<font_id>`` placeholders inside CSS are rewritten in memory
        before ``save_fonts_manifest`` is called, so no secondary write to the
        remote storage is necessary.

        ``on_finalize`` is optional: when omitted the SDK still persists every
        font binary and manifest via ``storage`` and returns the default
        payload that matches the Verstka contract. Pass an ``on_finalize``
        when the application needs to record the saved URLs, recompute caches,
        etc. ``on_pre_save`` mirrors the material-callback hook and can reject
        the whole operation before any ZIP is downloaded.
        """
        return self._processor.process_fonts_callback_sync(
            callback_data,
            storage=storage,
            on_finalize=on_finalize,
            on_pre_save=on_pre_save,
        )


def _coerce_json(
    value: str | Mapping[str, Any] | None, error_cls: type[Exception]
) -> dict[str, Any] | None:
    """Coerce ``value`` to a ``dict`` (accepts JSON string or mapping)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise error_cls(f"{error_cls.__name__}: {exc}") from exc
    else:
        parsed = dict(value)
    if not isinstance(parsed, dict):
        raise error_cls(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def _build_session_payload(
    config: VerstkaConfig,
    material_id: str,
    vms_json: str | Mapping[str, Any] | None,
    metadata: str | Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    if not material_id:
        raise VerstkaApiError("material_id is required")

    existing_metadata = _coerce_json(metadata, VerstkaMetadataJsonError) or {}
    merged_metadata: dict[str, Any] = {"version": "2.0", **existing_metadata}

    if config.basic_auth_user and config.basic_auth_password:
        merged_metadata["webhook_basic_auth_user"] = config.basic_auth_user
        merged_metadata["webhook_basic_auth_password"] = config.basic_auth_password

    payload: dict[str, Any] = {
        "api_key": config.api_key,
        "callback_url": config.callback_url,
        "material_id": material_id,
        "metadata": merged_metadata,
    }

    vms_json_dict = _coerce_json(vms_json, VerstkaVmsJsonError)
    if vms_json_dict is not None:
        payload["vms_json"] = vms_json_dict

    signature = sign_material(material_id, config.callback_url, config.api_secret)
    return payload, signature


def _parse_editor_response(response: httpx.Response) -> str:
    if response.status_code != 200:
        raise VerstkaApiError(
            f"Invalid verstka response: {response.status_code} {response.text}",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise VerstkaApiError(f"Non-JSON response from Verstka: {exc}") from exc
    if "url" not in data:
        raise VerstkaApiError(json.dumps(data))
    url = data["url"]
    if not isinstance(url, str) or not url:
        raise VerstkaApiError(f"Unexpected 'url' value: {url!r}")
    return url
