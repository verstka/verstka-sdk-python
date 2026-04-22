"""URL helpers for Verstka SDK."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def build_authorized_content_url(content_url: str, api_key: str, material_id: str) -> str:
    """Append ``api_key`` and ``material_id`` query parameters to ``content_url``.

    Existing query parameters are preserved. Empty values are skipped.
    """
    if not content_url:
        raise ValueError("content_url must be a non-empty string")

    parsed = urlparse(content_url)
    query_params: dict[str, list[str]] = parse_qs(parsed.query)

    extras = {"api_key": api_key, "material_id": material_id}
    for key, value in extras.items():
        if value:
            query_params[key] = [value]

    updated_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=updated_query))
