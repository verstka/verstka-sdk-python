# verstka-sdk


Features:

- Sync (`VerstkaClient`) and async (`AsyncVerstkaClient`) clients, both on
  `httpx`.
- Framework-agnostic core. Pluggable integrations for **FastAPI**, **Flask**,
  **Django** (async views), and **Django REST Framework**.
- HMAC-SHA256 signature verification for incoming callbacks.
- Streaming ZIP download with a configurable size cap and path-traversal
  protection.
- Automatic extraction of `vms_media/*`, `vms_json.json`, `vms_html.html`,
  and font bundles (`vms_fonts/*`, `vms_fonts.json`, `vms_fonts.css`).
- Automatic `dummy-*` replacement in HTML/CSS and `clientUrl` updates inside
  `vms_json.assets` and the fonts tree.
- Storage-adapter contract (`StorageAdapter` / `AsyncStorageAdapter`) with a
  reference filesystem implementation; bring your own S3/GCS/CDN backend.
- Typed `on_finalize` callback with a full context object (saved URLs,
  rewritten `vms_json`/`vms_html`, metadata, etc.).
- Optional **PreSave** hooks (`on_pre_save`): run extra checks **before** any
  ZIP download or storage write — e.g. allow/deny by editor-supplied
  `metadata["user_email"]` / `metadata["user_ip"]` (reserved keys, refreshed
  on every save) or by any **custom** keys you set in `metadata` when opening
  the editor session.
Python SDK for [Verstka](https://verstka.org) API v2. Handles signatures,
`session/open`, callback processing, ZIP download, and media/font persistence
through a storage adapter.

## Installation

```bash
pip install verstka-sdk              # core only
pip install 'verstka-sdk[fastapi]'   # + FastAPI integration
pip install 'verstka-sdk[flask]'     # + Flask integration
pip install 'verstka-sdk[django]'    # + Django integration
pip install 'verstka-sdk[drf]'       # + Django REST Framework integration
pip install 'verstka-sdk[all]'       # all integrations
```

Minimum Python version: 3.10.

## Configuration

```python
from verstka_sdk import VerstkaConfig

config = VerstkaConfig(
    api_key="verstka-api-key",
    api_secret="verstka-api-secret",
    callback_url="https://site.example/verstka/callback",
    api_url="https://api.r2.verstka.org/integration",
    max_content_size=200 * 1024 * 1024,
    request_timeout=60.0,
    download_timeout=120.0,
    debug=False,
)
```

`api_url` defaults to `https://api.r2.verstka.org/integration`.
`max_content_size` defaults to 200 MiB when omitted.

## Main methods

| Method | Where | Purpose |
| --- | --- | --- |
| `VerstkaClient.get_editor_url(...)` | sync | Opens a session via `POST /session/open` and returns the editor URL. |
| `AsyncVerstkaClient.get_editor_url(...)` | async | Same for async applications. |
| `VerstkaClient.process_material_callback(...)` | sync | Handles `article_saved`: signature, ZIP, media, `on_finalize`. |
| `AsyncVerstkaClient.process_material_callback(...)` | async | Async `article_saved` handler. |
| `VerstkaClient.process_fonts_callback(...)` | sync | Handles `site_fonts_updated`: signature, fonts ZIP, font files, manifests. |
| `AsyncVerstkaClient.process_fonts_callback(...)` | async | Async `site_fonts_updated` handler. |
| `LocalStorageAdapter` | sync | Filesystem reference storage adapter. |
| `LocalAsyncStorageAdapter` | async | Async reference storage adapter. |
| `sign_material(...)` | helper | Builds HMAC for `material_id:url`. |
| `verify_signature(...)` | helper | Verifies HMAC safely. |
| `build_authorized_content_url(...)` | helper | Adds `api_key` and `material_id` to `content_url` for ZIP download. |

## Open editor

```python
from verstka_sdk import AsyncVerstkaClient, VerstkaConfig

async def open_editor(material_id: str, vms_json: dict | None) -> str:
    async with AsyncVerstkaClient(VerstkaConfig(...)) as client:
        return await client.get_editor_url(
            material_id=material_id,
            vms_json=vms_json,
            metadata={
                # optional: "anySiteAdditionalKey": "anySiteAdditionalValue",
                # optional: "timeLimitedAuthToken": "cms-scope-token",
                # optional: "customContainers": {},
                # optional: "webhook_auth_user": "callback-user", # (see Callback Authorization)
                # optional: "webhook_auth_password": "callback-password",
            },
        )
```

Both `vms_json` and `metadata` accept a `dict` or a JSON string. The SDK sends
`metadata` as a JSON object and automatically adds
`version: "python_<sdk-version>"`. For Basic Auth or a Bearer token, pass
`webhook_auth_user` and optionally `webhook_auth_password` in `metadata` when
calling `get_editor_url` (see
[Callback Authorization](https://docs.r2.verstka.org/ru/dev/api-integration.md#callback-authorization)).

Sites usually pass `timeLimitedAuthToken` and `customContainers`, plus any site-specific keys (e.g. `anySiteAdditionalKey`: `anySiteAdditionalValue`).
Other custom keys are allowed — see
[`metadata`](https://docs.r2.verstka.org/ru/dev/api-integration.md#metadata) in the API
docs. Values you pass are echoed in callbacks and available in `StorageAdapter`,
`on_pre_save`, and `on_finalize`.

Service keys: `version_id`, `version_cdate`, `user_email`, `user_ip` — the
Verstka backend adds or updates them in the callback after save;
`webhook_auth_user` and `webhook_auth_password` authorize the outgoing callback
(see
[Callback Authorization](https://docs.r2.verstka.org/ru/dev/api-integration.md#callback-authorization)).
Your callback handler usually ignores `webhook_auth_*`.

Open the editor in a separate tab:

```html
<a href="/getEditorUrlScript" target="_blank" rel="noopener noreferrer">
  Edit in Verstka
</a>
```

## StorageAdapter

The SDK does not know where your site stores files — the adapter does.

```python
from pathlib import Path
from collections.abc import Mapping
from typing import Any

class StorageAdapter:
    def save_media(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...

    def save_font_file(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...

    def save_fonts_manifest(
        self,
        filename: str,
        temp_path: Path,
        material_id: str,
        metadata: Mapping[str, Any],
    ) -> str: ...
```

`AsyncStorageAdapter` is the async twin — each method returns an awaitable.

Every call receives the trailing `(material_id, metadata)` pair so multi-tenant
adapters can route writes by `metadata["AnyOtherKey"]`, `metadata["tenant"]`,
environment, etc.

The SDK ships with filesystem-backed reference implementations:

```python
from verstka_sdk import LocalStorageAdapter, LocalAsyncStorageAdapter

storage = LocalStorageAdapter(
    root="/var/www/example.com/public/static/verstka-media",
    base_url="https://cdn.example.com",
)
# → media:   /var/www/example.com/public/static/verstka-media/materials/<material_id>/<filename>
# → fonts:   /var/www/example.com/public/static/verstka-media/fonts/<filename>
# → URL:     https://cdn.example.com/materials/<material_id>/<filename>
```

Writing a custom adapter (e.g. S3):

```python
class S3Storage:
    def __init__(self, bucket: str, cdn_url: str) -> None:
        self.bucket = bucket
        self.cdn_url = cdn_url.rstrip("/")

    def save_media(self, filename, temp_path, material_id, metadata):
        key = f"materials/{material_id}/{filename}"
        s3_client.upload_file(str(temp_path), self.bucket, key)
        return f"{self.cdn_url}/{key}"

    def save_font_file(self, filename, temp_path, material_id, metadata):
        key = f"fonts/{filename}"
        s3_client.upload_file(str(temp_path), self.bucket, key)
        return f"{self.cdn_url}/{key}"

    def save_fonts_manifest(self, filename, temp_path, material_id, metadata):
        return self.save_font_file(filename, temp_path, material_id, metadata)
```

## Handling the material callback

The JSON body that Verstka POSTs to your `callback_url` looks like:

```json
{
  "material_id": "42",
  "content_url": "https://api.r2.verstka.org/integration/download/<token>",
  "metadata": {
    "userId": 11,
    "AnyOtherKey": "AnyOtherVal",
    "user_email": "author@example.com",
    "user_ip": "203.0.113.10"
  }
}
```

Each method must persist the file and return a public URL. The SDK substitutes
these URLs into `vms_html`, `vms_json.assets[*].clientUrl`, `vms_fonts.css`, and
the `fonts` tree.

You provide:

- A `StorageAdapter`/`AsyncStorageAdapter` — the SDK calls `save_media` for
  every file found under `vms_media/`.
- An `on_finalize` callback — invoked once, after all IO, with a typed
  `ContentFinalizeContext`.
- Optionally an `on_pre_save` callback — see
  [Access control via `on_pre_save` hooks](#access-control-via-on_pre_save-hooks).
  Use it for **extra validation** on `ctx.metadata` (your session keys merged
  with editor-reserved `user_email` / `user_ip`, which are refreshed on every
  save) **before** the content ZIP is downloaded.
## Material callback

```python
from verstka_sdk import (
    AsyncVerstkaClient,
    ContentFinalizeContext,
    ContentFinalizeResult,
)

async def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    await db.save_article(
        material_id=ctx.material_id,
        html=ctx.vms_html,
        vms_json=ctx.vms_json,
        metadata=dict(ctx.metadata),
    )
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)

result = await client.process_material_callback(
    callback_data,
    signature=request.headers.get("X-Verstka-Signature", ""),
    storage=storage,
    on_finalize=on_content_finalize,
)

return result.to_response()
```

`ContentFinalizeContext`:

| Field | Purpose |
| --- | --- |
| `material_id` | Material ID from your CMS. |
| `metadata` | Metadata from the callback. |
| `vms_json` | Article JSON with updated `clientUrl` values. |
| `vms_html` | Article HTML with `dummy-*` URLs replaced. |
| `saved_media_urls` | `{filename: public_url}` map. |

## Fonts callback

```python
from verstka_sdk import FontsFinalizeContext, FontsFinalizeResult

async def on_fonts_finalize(ctx: FontsFinalizeContext) -> FontsFinalizeResult:
    await db.save_site_fonts(
        fonts=ctx.fonts,
        css_url=ctx.css_url,
        json_url=ctx.json_url,
    )
    return FontsFinalizeResult(success=True, fonts=ctx.fonts)

result = await client.process_fonts_callback(
    callback_data,
    signature=request.headers.get("X-Verstka-Signature", ""),
    storage=storage,
    on_finalize=on_fonts_finalize,
)
```

`on_finalize` for fonts is optional. Without it, the SDK still saves files via
`storage` and returns the `fonts` tree with `clientUrl` to Verstka.

## PreSave hooks

Both callback methods accept `on_pre_save`. The hook runs after signature
verification but before ZIP download — use it for permissions, locks, quotas,
and tenant policy.

```python
from verstka_sdk import ContentPreSaveContext, PreSaveDecision

def can_save(ctx: ContentPreSaveContext) -> PreSaveDecision:
    if not user_can_edit(ctx.metadata.get("timeLimitedAuthToken"), ctx.material_id):
        return PreSaveDecision(allow=False, reason="Access denied")
    return PreSaveDecision(allow=True)
```

If `allow=False`, the SDK skips the ZIP download, writes no files, and responds
to Verstka with `rc: 0`.

## Unified callback endpoint

Use one callback route for both material and fonts events. Import dispatch helpers
from `verstka_sdk.integrations._base` — the same functions used inside FastAPI,
Flask, Django, and DRF adapters:

```python
from verstka_sdk import (
    AsyncVerstkaClient,
    ContentFinalizeContext,
    ContentFinalizeResult,
    FontsFinalizeContext,
    FontsFinalizeResult,
)
from verstka_sdk.integrations._base import dispatch_callback_async, map_exception

async def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    await db.save_article(
        material_id=ctx.material_id,
        html=ctx.vms_html,
        vms_json=ctx.vms_json,
    )
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)

async def on_fonts_finalize(ctx: FontsFinalizeContext) -> FontsFinalizeResult:
    await db.save_site_fonts(fonts=ctx.fonts, css_url=ctx.css_url)
    return FontsFinalizeResult(success=True, fonts=ctx.fonts)

async def verstka_callback(request):
    callback_data = await request.json()
    signature = request.headers.get("X-Verstka-Signature", "")

    try:
        async with AsyncVerstkaClient(config) as client:
            return await dispatch_callback_async(
                client,
                callback_data,
                signature,
                storage=storage,
                on_content_finalize=on_content_finalize,
                on_fonts_finalize=on_fonts_finalize,
            )
    except Exception as exc:
        error = map_exception(exc)
        return JSONResponse(error.to_dict(), status_code=error.status)
```

For sync apps, use `dispatch_callback_sync` with `VerstkaClient` instead.

The dispatcher reads `callback_data["event"]`: `site_fonts_updated` routes to the
fonts flow, everything else to the material flow.

## Framework integrations

| Framework | Tooling |
| --- | --- |
| FastAPI | `install_exception_handlers(app)`, `build_callback_router(...)` |
| Flask | `register_error_handlers(app)`, `build_blueprint(...)` |
| Django | `build_callback_views(...)`, `VerstkaExceptionMiddleware` |
| DRF | `build_callback_views(...)`, `verstka_exception_handler` |

Framework adapters register a single callback endpoint and call
`dispatch_callback_async` or `dispatch_callback_sync` internally.

## Documentation

Full integration guide (Russian):
[frontend/docs/ru/dev/sdk-python.md](https://docs.r2.verstka.org/ru/dev/sdk-python.md)

Related: [API integration](https://docs.r2.verstka.org/ru/dev/ru/dev/api-integration.md),
[site integration](https://docs.r2.verstka.org/ru/dev/site-integration.md).

## License

MIT — see [LICENSE](LICENSE).
