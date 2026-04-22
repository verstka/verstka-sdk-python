"""Tests for ``build_authorized_content_url``."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from verstka_sdk.urls import build_authorized_content_url


def test_adds_api_key_and_material_id() -> None:
    url = build_authorized_content_url("https://x.test/content", "KEY", "M1")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params == {"api_key": ["KEY"], "material_id": ["M1"]}


def test_preserves_existing_params() -> None:
    url = build_authorized_content_url("https://x.test/c?foo=1&bar=2", "K", "M")
    params = parse_qs(urlparse(url).query)
    assert params["foo"] == ["1"]
    assert params["bar"] == ["2"]
    assert params["api_key"] == ["K"]
    assert params["material_id"] == ["M"]


def test_overrides_existing_auth_params() -> None:
    url = build_authorized_content_url("https://x.test/c?api_key=OLD", "NEW", "M")
    params = parse_qs(urlparse(url).query)
    assert params["api_key"] == ["NEW"]


def test_empty_values_are_skipped() -> None:
    url = build_authorized_content_url("https://x.test/c?keep=1", "", "")
    params = parse_qs(urlparse(url).query)
    assert "api_key" not in params
    assert "material_id" not in params
    assert params["keep"] == ["1"]


def test_requires_url() -> None:
    with pytest.raises(ValueError):
        build_authorized_content_url("", "K", "M")
