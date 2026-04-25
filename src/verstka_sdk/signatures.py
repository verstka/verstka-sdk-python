"""HMAC-SHA256 signature helpers for Verstka API v2.

Verstka signs both outgoing requests (``session/open``) and incoming callbacks
with the same formula:

    signature = hex(HMAC_SHA256(secret, f"{material_id}:{url}"))

where ``url`` is the ``callback_url`` for outgoing requests and the
``content_url`` for incoming callbacks. The resulting digest is always placed
in the ``X-Verstka-Signature`` HTTP header.
"""

from __future__ import annotations

import hashlib
import hmac


def sign_material(material_id: str, url: str, secret: str) -> str:
    """Return hex HMAC-SHA256 signature for ``"{material_id}:{url}"``.

    Args:
        material_id: Material identifier passed to Verstka.
        url: Either ``callback_url`` (outgoing) or ``content_url`` (incoming).
        secret: Shared API secret.
    """
    if not secret:
        raise ValueError("secret is required to compute a signature")
    msg = f"{material_id}:{url}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(
    material_id: str,
    url: str,
    signature: str,
    secret: str,
) -> bool:
    """Constant-time check that ``signature`` matches the expected HMAC."""
    if not signature:
        return False
    expected = sign_material(material_id, url, secret)
    return hmac.compare_digest(expected, signature)
