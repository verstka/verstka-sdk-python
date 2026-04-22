"""Configuration model for Verstka SDK clients."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_API_URL = "https://api.r2.verstka.org/integration"
DEFAULT_MAX_CONTENT_SIZE = 100 * 1024 * 1024  # 100 MB


class VerstkaConfig(BaseModel):
    """Runtime configuration for ``VerstkaClient`` / ``AsyncVerstkaClient``.

    All settings that previously lived in ``app.config.get_settings`` are
    consolidated here; pass a single instance to the client constructor.
    """

    model_config = ConfigDict(extra="ignore", frozen=False, populate_by_name=True)

    api_key: str = Field(..., description="Verstka API key used on session/open and content download.")
    api_secret: str = Field(..., description="Shared secret for HMAC-SHA256 signatures.")
    callback_url: str = Field(..., description="Public URL Verstka will POST callbacks to.")
    api_url: str = Field(
        default=DEFAULT_API_URL,
        description="Base URL of Verstka API (e.g. https://api.r2.verstka.org/integration).",
    )
    basic_auth_user: str | None = Field(default=None, description="Optional HTTP basic auth user for callback URL.")
    basic_auth_password: str | None = Field(default=None, description="Optional HTTP basic auth password for callback URL.")
    max_content_size: int = Field(
        default=DEFAULT_MAX_CONTENT_SIZE,
        ge=1,
        description="Maximum size of downloaded content ZIP in bytes.",
    )
    request_timeout: float = Field(default=60.0, gt=0, description="Timeout (seconds) for API requests.")
    download_timeout: float = Field(default=120.0, gt=0, description="Timeout (seconds) for content downloads.")
    debug: bool = Field(default=False, description="Include debug information in callback responses and error messages.")

    @property
    def session_open_url(self) -> str:
        """Full URL of the ``session/open`` endpoint."""
        return f"{self.api_url.rstrip('/')}/session/open"
