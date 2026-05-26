"""Flask integration (optional extra ``flask``).

Importing this module requires Flask (``pip install verstka-sdk[flask]``).
"""

from __future__ import annotations

from typing import Any

try:
    from flask import Blueprint, Flask, jsonify, request
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "flask is required for this integration. "
        "Install with: pip install 'verstka-sdk[flask]'"
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
from ._base import is_fonts_callback_payload, map_exception


def register_error_handlers(app: Flask) -> None:
    """Attach a JSON error handler for ``VerstkaError`` on a Flask app."""

    def _handler(exc: VerstkaError) -> Any:
        mapped = map_exception(exc)
        return jsonify(mapped.to_dict()), mapped.status

    app.register_error_handler(VerstkaError, _handler)


def build_blueprint(
    client: VerstkaClient,
    *,
    storage: StorageAdapter,
    on_content_finalize: ContentFinalizeFn,
    on_fonts_finalize: FontsFinalizeFn | None = None,
    on_content_pre_save: ContentPreSaveFn | None = None,
    on_fonts_pre_save: FontsPreSaveFn | None = None,
    url_prefix: str = "/verstka",
    name: str = "verstka",
) -> Blueprint:
    """Return a Flask ``Blueprint`` exposing callback endpoints.

    The ``/callback`` route handles both material callbacks and
    ``site_fonts_updated`` font events. ``on_fonts_finalize`` is optional when
    the application only needs the SDK to persist fonts through ``storage``.
    ``on_*_pre_save`` hooks can reject the whole operation before any ZIP is
    downloaded based on ``material_id``/``metadata``.
    """
    blueprint = Blueprint(name, __name__, url_prefix=url_prefix)

    @blueprint.route("/callback", methods=["POST"])
    def _material_callback() -> Any:
        payload = request.get_json(force=True, silent=False) or {}
        signature = (request.headers.get("X-Verstka-Signature") or "").strip()
        if is_fonts_callback_payload(payload):
            fonts_result = client.process_fonts_callback(
                payload,
                signature=signature,
                storage=storage,
                on_finalize=on_fonts_finalize,
                on_pre_save=on_fonts_pre_save,
            )
            return jsonify(fonts_result.to_response())

        material_result = client.process_material_callback(
            payload,
            signature=signature,
            storage=storage,
            on_finalize=on_content_finalize,
            on_pre_save=on_content_pre_save,
        )
        return jsonify(material_result.to_response())

    return blueprint
