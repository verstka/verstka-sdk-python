"""Session/open request helpers shared by sync and async clients."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from .config import VerstkaConfig
from .exceptions import VerstkaApiError, VerstkaMetadataJsonError, VerstkaVmsJsonError
from .signatures import sign_material


def coerce_json(
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


def build_session_payload(
    config: VerstkaConfig,
    material_id: str,
    vms_json: str | Mapping[str, Any] | None,
    metadata: str | Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    if not material_id:
        raise VerstkaApiError("material_id is required")

    from verstka_sdk import __version__

    existing_metadata = coerce_json(metadata, VerstkaMetadataJsonError) or {}
    merged_metadata: dict[str, Any] = {
        "version": f"python_{__version__}",
        **existing_metadata,
    }

    payload: dict[str, Any] = {
        "api_key": config.api_key,
        "callback_url": config.callback_url,
        "material_id": material_id,
        "metadata": merged_metadata,
    }

    vms_json_dict = coerce_json(vms_json, VerstkaVmsJsonError)
    if vms_json_dict is not None:
        payload["vms_json"] = vms_json_dict

    signature = sign_material(material_id, config.callback_url, config.api_secret)
    return payload, signature


def parse_editor_response(response: httpx.Response) -> str:
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
