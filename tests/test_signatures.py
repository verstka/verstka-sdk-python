"""Tests for HMAC signature helpers."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from verstka_sdk.signatures import sign_material, verify_signature


def test_sign_matches_reference_hmac() -> None:
    secret = "my-secret"
    material_id = "42"
    url = "https://example.com/content"

    signature = sign_material(material_id, url, secret)

    expected = hmac.new(
        secret.encode(), f"{material_id}:{url}".encode(), hashlib.sha256
    ).hexdigest()
    assert signature == expected


def test_verify_signature_ok() -> None:
    secret = "my-secret"
    signature = sign_material("42", "https://x.test", secret)
    assert verify_signature("42", "https://x.test", signature, secret) is True


def test_verify_signature_reject_wrong_value() -> None:
    assert verify_signature("42", "https://x.test", "not-a-sig", "secret") is False


def test_verify_signature_rejects_empty() -> None:
    assert verify_signature("42", "https://x.test", "", "secret") is False


def test_sign_requires_secret() -> None:
    with pytest.raises(ValueError):
        sign_material("42", "https://x.test", "")
