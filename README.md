# verstka-sdk

Python SDK for [Verstka](https://verstka.org) API v2. Open editor sessions,
verify and process callbacks (material content + site fonts), and plug the
result into your web framework of choice.

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

## Installation

```bash
pip install verstka-sdk              # core only
pip install 'verstka-sdk[fastapi]'   # + FastAPI integration
pip install 'verstka-sdk[flask]'     # + Flask integration
pip install 'verstka-sdk[django]'    # + Django integration
pip install 'verstka-sdk[drf]'       # + Django REST Framework integration
pip install 'verstka-sdk[all]'       # all integrations
```

Python 3.10+ is required.

## Configuration

All runtime settings live on a single `VerstkaConfig` model that you pass to
the client:

```python
from verstka_sdk import VerstkaConfig

config = VerstkaConfig(
    api_key="verstka-api-key",
    api_secret="verstka-api-secret",
    callback_url="https://app.example.com/verstka/callback",
    api_url="https://api.r2.verstka.org/integration",   # optional, defaults to prod
    max_content_size=100 * 1024 * 1024,      # 100 MiB, default
    request_timeout=60.0,
    download_timeout=120.0,
    basic_auth_user=None,                    # optional HTTP basic auth for callbacks
    basic_auth_password=None,
    debug=False,                             # include extra info on errors
)
```

## Quickstart

### Open an editor session (async)

```python
from verstka_sdk import AsyncVerstkaClient, VerstkaConfig

async def open_editor(material_id: str, vms_json: dict | None) -> str:
    config = VerstkaConfig(...)
    async with AsyncVerstkaClient(config) as client:
        return await client.get_editor_url(
            material_id=material_id,
            vms_json=vms_json,
            metadata={
                "userId": 11,
                "userIP": "127.0.0.1",
                "AnyOtherKey": "AnyOtherVal",
            },
        )
```

Both `vms_json` and `metadata` accept either a `dict` or a JSON string.

### Reserved `metadata` keys (editor)

The Verstka editor reserves **`user_email`** and **`user_ip`**. On every save
(callback), it **sets or overwrites** these keys in the `metadata` object you
receive — values you might pass in `get_editor_url` for those names are **not**
preserved across saves. Use your own keys (for example `AnyOtherKey`, `internalUserId`,
`fonts_callback_allowed`) for values your backend must trust; treat
`user_email` / `user_ip` in callbacks as **editor-supplied** identity and
network context for policy checks (e.g. PreSave allowlists).

Fields you add under `metadata` (other than the reserved names above) are echoed
in save callbacks together with the editor-controlled keys. That lets you
implement **PreSave** validation in your webhook (see
[Access control via `on_pre_save` hooks](#access-control-via-on_pre_save-hooks))
without trusting client-supplied headers alone — as long as you only set
trusted keys on the server when calling `get_editor_url`.

### Open an editor session (sync)

```python
from verstka_sdk import VerstkaClient, VerstkaConfig

with VerstkaClient(VerstkaConfig(...)) as client:
    url = client.get_editor_url(material_id="42")
```

## Storage adapters

The SDK never writes to your storage directly — it delegates to an adapter
that you provide. Adapters implement one of two protocols:

```python
from pathlib import Path
from collections.abc import Mapping
from typing import Any

class StorageAdapter:  # sync contract (used by VerstkaClient)
    def save_media(
        self, filename: str, temp_path: Path,
        material_id: str, metadata: Mapping[str, Any],
    ) -> str: ...

    def save_font_file(
        self, filename: str, temp_path: Path,
        material_id: str, metadata: Mapping[str, Any],
    ) -> str: ...

    def save_fonts_manifest(
        self, filename: str, temp_path: Path,
        material_id: str, metadata: Mapping[str, Any],
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

The HMAC-SHA256 digest is sent in the **`X-Verstka-Signature`** HTTP header (same
formula as for `session/open`), not inside the JSON body.

(`user_email` and `user_ip` are written by the editor on each save; your own
keys such as `siteId` are merged from the session.)

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

```python
from verstka_sdk import (
    AsyncVerstkaClient,
    ContentFinalizeContext,
    ContentFinalizeResult,
)

async def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    # ctx.vms_html and ctx.vms_json already have all `dummy-<filename>`
    # placeholders replaced with public URLs returned by storage.save_media.
    await db.save_material(
        material_id=ctx.material_id,
        html=ctx.vms_html,
        state=ctx.vms_json,
        metadata=ctx.metadata,
    )
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)


async with AsyncVerstkaClient(config) as client:
    result = await client.process_material_callback(
        callback_data,
        signature=request.headers.get("X-Verstka-Signature", ""),
        storage=storage,
        on_finalize=on_content_finalize,
    )

return result.to_response()
# → {"rc": 1, "rm": "Saved successfully", "data": {"vms_json": {...}}}
```

`ContentFinalizeContext` exposes:

| Field              | Description                                                       |
|--------------------|-------------------------------------------------------------------|
| `material_id`      | Material identifier from the callback payload.                    |
| `metadata`         | `metadata` dict from the callback payload (tenant/site routing).  |
| `vms_json`         | Rewritten VMS state (`assets[*].clientUrl` updated).              |
| `vms_html`         | Rewritten HTML (`dummy-<filename>` replaced).                     |
| `saved_media_urls` | `{filename: public_url}` map returned by `storage.save_media`.    |

`ContentFinalizeResult(success, vms_json=None)` — `vms_json` is included in
the HTTP response body under `data.vms_json` if non-`None`.

The SDK does, in order:

1. Verify the HMAC-SHA256 signature from the ``X-Verstka-Signature`` header
   (`VerstkaSignatureError` on mismatch).
2. Optionally run `on_pre_save(ContentPreSaveContext)` — validate policy on
   `metadata` (and `material_id`, `content_url`) **before** any download or
   storage. If it returns `PreSaveDecision(allow=False)`, the flow stops here
   with `rc=0` (see [Access control via `on_pre_save` hooks](#access-control-via-on_pre_save-hooks)).
3. Stream the content ZIP, refusing to exceed `max_content_size`.
4. Extract `vms_media/*` into a temp dir and call `storage.save_media` for
   each file.
5. Replace every `dummy-<filename>` in `vms_html` with the returned URL and
   update `vms_json["assets"][<filename>]["clientUrl"]`.
6. Invoke `on_finalize(ctx)` and use its `ContentFinalizeResult` to shape the
   HTTP response.
7. Remove the temp dir, even on errors.

Sync version uses `VerstkaClient` with plain synchronous callables:

```python
def on_content_finalize(ctx: ContentFinalizeContext) -> ContentFinalizeResult:
    db.save_material(ctx.material_id, ctx.vms_html, ctx.vms_json)
    return ContentFinalizeResult(success=True, vms_json=ctx.vms_json)

with VerstkaClient(config) as client:
    result = client.process_material_callback(
        callback_data,
        signature=request.headers.get("X-Verstka-Signature", ""),
        storage=storage,
        on_finalize=on_content_finalize,
    )
```

## Handling the fonts callback

Verstka emits a separate `site_fonts_updated` callback when site-level fonts
change. The payload carries a `fonts` tree and a `content_url` pointing at a
ZIP with `vms_fonts/*` font binaries and the `vms_fonts.json` / `vms_fonts.css`
manifests.

The SDK:

1. Verifies the signature from the ``X-Verstka-Signature`` header.
2. Optionally runs `on_pre_save(FontsPreSaveContext)` — same idea as for
   material: validate **before** download/storage; on `allow=False` the flow
   stops with `rc=0` (see [Access control via `on_pre_save` hooks](#access-control-via-on_pre_save-hooks)).
3. Downloads + extracts the ZIP.
4. Calls `storage.save_font_file(filename, temp_path, material_id, metadata)`
   for every binary in `vms_fonts/`.
5. Rewrites `dummy-<font_id>` placeholders inside `vms_fonts.css` **in
   memory** before persisting it — your remote storage only receives the
   final CSS once.
6. Calls `storage.save_fonts_manifest(...)` for both `vms_fonts.css` and
   `vms_fonts.json` with the same `(material_id, metadata)` tail.
7. Fills `clientUrl` fields throughout the `fonts` payload.
8. Invokes `on_finalize(FontsFinalizeContext)` **when it is provided** and
   builds the HTTP response.

`on_finalize` is **optional** for the fonts flow. When it is omitted the SDK
still persists every font binary and manifest through `storage` and returns
the default payload to Verstka. This is the right default when your templates
already reference deterministic storage URLs and no extra application state
needs to change on each fonts update.

```python
from verstka_sdk import (
    AsyncVerstkaClient,
    FontsFinalizeContext,
    FontsFinalizeResult,
    LocalAsyncStorageAdapter,
)

storage = LocalAsyncStorageAdapter(
    root="/var/www/example.com/public/static/verstka-media",
    base_url="https://cdn.example.com",
)

async def on_fonts_finalize(ctx: FontsFinalizeContext) -> FontsFinalizeResult:
    await db.save_site_fonts(
        any_other_key=ctx.metadata.get("AnyOtherKey"),
        fonts=ctx.fonts,
        css_url=ctx.css_url,
        json_url=ctx.json_url,
    )
    return FontsFinalizeResult(success=True, fonts=ctx.fonts)


async with AsyncVerstkaClient(config) as client:
    sig = request.headers.get("X-Verstka-Signature", "")
    # With on_finalize — records the saved URLs in the database.
    result = await client.process_fonts_callback(
        callback_data,
        signature=sig,
        storage=storage,
        on_finalize=on_fonts_finalize,
    )

    # Without on_finalize — fonts are still persisted through storage.
    result = await client.process_fonts_callback(
        callback_data, signature=sig, storage=storage
    )

return result.to_response()
```

`FontsFinalizeContext` exposes:

| Field             | Description                                                |
|-------------------|------------------------------------------------------------|
| `material_id`     | Identifier from the callback payload (often a post id).    |
| `metadata`        | `metadata` dict from the callback payload.                 |
| `fonts`           | Fonts tree with `clientUrl` filled in (binaries + CSS).    |
| `css_url`         | Public URL of the saved `vms_fonts.css`, or `None`.        |
| `json_url`        | Public URL of the saved `vms_fonts.json`, or `None`.       |
| `saved_font_urls` | `{font_id: url}` map returned by `storage.save_font_file`. |

## Access control via `on_pre_save` hooks

Every `process_*_callback` method accepts an optional `on_pre_save` hook that
runs **after** signature verification and **before** any ZIP download or
storage write. It receives a lightweight context (`material_id`, `metadata`,
`content_url`, and for fonts also the declared `fonts` tree) and returns a
`PreSaveDecision`.

### PreSave: extra validation via `metadata`

PreSave is the right place for **policy and validation** that only needs
identifiers and business context — not the full ZIP. Typical inputs are your
keys from `get_editor_url` **plus** editor-reserved `user_email` and `user_ip`
(see [Reserved `metadata` keys (editor)](#reserved-metadata-keys-editor)); the
latter are **overwritten by the editor on every save**, so they reflect the
current save context, not values you tried to set at session open. Examples:

| Check | Idea |
|-------|------|
| `metadata["user_email"]` | Editor-supplied on each save — deny if blocked, disposable-domain list, or not in your org’s allowlist. |
| `metadata["AnyOtherKey"]`  | Reject if the key is disabled or not in your allowlist. |
| `FontsPreSaveContext.fonts` | Inspect the declared font tree (family names, file ids) against rules your backend owns — e.g. blocklist of font ids, or match against `metadata` keys your server set at session open. |
| Required keys | Return `PreSaveDecision(allow=False, reason="...")` if a required **custom** key (e.g. `siteId`) is missing, or if your policy requires `user_email` but the editor did not supply it. |

Because PreSave runs **before** `storage.save_*`, a rejection does not write
partial files to your bucket or disk.

```python
from verstka_sdk import (
    ContentPreSaveContext,
    FontsPreSaveContext,
    PreSaveDecision,
)

BLACKLIST = {"blocked@example.com"}

def reject_blacklisted(ctx: ContentPreSaveContext) -> PreSaveDecision:
    email = ctx.metadata.get("user_email")
    if not isinstance(email, str) or "@" not in email:
        return PreSaveDecision(allow=False, reason="Invalid user_email in metadata")
    if email.lower() in BLACKLIST:
        return PreSaveDecision(allow=False, reason="User blacklisted")
    return PreSaveDecision(allow=True)

def reject_fonts_unless_flag(ctx: FontsPreSaveContext) -> PreSaveDecision:
    # Your server sets this when opening the editor (client never invents it).
    if not ctx.metadata.get("fonts_callback_allowed"):
        return PreSaveDecision(allow=False, reason="Fonts callback not enabled")
    return PreSaveDecision(allow=True)

result = client.process_material_callback(
    callback_data,
    signature=request.headers.get("X-Verstka-Signature", ""),
    storage=storage,
    on_finalize=on_content_finalize,
    on_pre_save=reject_blacklisted,
)
```

When `PreSaveDecision(allow=False, reason=...)` is returned the SDK:

- skips the ZIP download entirely (no bandwidth is spent on rejected payloads);
- skips every `storage.save_*` call and the `on_finalize` hook;
- responds to Verstka with `rc=0` and the provided `reason` as `rm`.

The async client expects an async callable with the same signature. A reason
of `None` falls back to `"Operation rejected"` in the HTTP body.

## Framework integrations

All integrations are optional. The SDK's core code raises only its own
`VerstkaError` subclasses; each integration maps them to JSON responses:

| Exception                   | HTTP status | `code`                    |
|-----------------------------|-------------|---------------------------|
| `VerstkaSignatureError`     | 400         | `invalid_signature`       |
| `VerstkaCallbackDataError`  | 400         | `invalid_callback_data`   |
| `VerstkaVmsJsonError`       | 400         | `invalid_vms_json`        |
| `VerstkaMetadataJsonError`  | 400         | `invalid_metadata_json`   |
| `VerstkaApiError`           | `status_code` or 502 | `verstka_api_error` |
| `VerstkaError` (other)      | 500         | `verstka_error`           |

Response body shape: `{"error": "<code>", "code": "<code>", "message": "..."}`.

### FastAPI

```python
from fastapi import FastAPI
from verstka_sdk import AsyncVerstkaClient, LocalAsyncStorageAdapter, VerstkaConfig
from verstka_sdk.integrations.fastapi import (
    install_exception_handlers,
    build_callback_router,
)

app = FastAPI()
client = AsyncVerstkaClient(VerstkaConfig(...))
storage = LocalAsyncStorageAdapter(root="/srv/media", base_url="https://cdn.example.com")

install_exception_handlers(app)
app.include_router(
    build_callback_router(
        client,
        storage=storage,
        on_content_finalize=on_content_finalize,
        on_fonts_finalize=on_fonts_finalize,   # optional
        on_content_pre_save=reject_blacklisted,  # optional access-control hook
        on_fonts_pre_save=reject_fonts_unless_flag,  # optional access-control hook
    )
)
# Always registers both POST /verstka/callback and POST /verstka/fonts-callback.
```

### Flask

```python
from flask import Flask
from verstka_sdk import LocalStorageAdapter, VerstkaClient, VerstkaConfig
from verstka_sdk.integrations.flask import (
    register_error_handlers,
    build_blueprint,
)

app = Flask(__name__)
client = VerstkaClient(VerstkaConfig(...))
storage = LocalStorageAdapter(root="/srv/media", base_url="https://cdn.example.com")

register_error_handlers(app)
app.register_blueprint(
    build_blueprint(
        client,
        storage=storage,
        on_content_finalize=on_content_finalize,
        on_fonts_finalize=on_fonts_finalize,   # optional
    )
)
```

### Django (async views)

```python
# urls.py
from django.urls import path
from verstka_sdk import AsyncVerstkaClient, LocalAsyncStorageAdapter, VerstkaConfig
from verstka_sdk.integrations.django import build_callback_views

client = AsyncVerstkaClient(VerstkaConfig(...))
storage = LocalAsyncStorageAdapter(root="/srv/media", base_url="https://cdn.example.com")

views = build_callback_views(
    client,
    storage=storage,
    on_content_finalize=on_content_finalize,
    on_fonts_finalize=on_fonts_finalize,   # optional
)

urlpatterns = [
    path("verstka/callback/", views["callback"]),
    path("verstka/fonts-callback/", views["fonts_callback"]),
]
```

Optionally install the exception middleware:

```python
# settings.py
MIDDLEWARE = [
    ...,
    "verstka_sdk.integrations.django.VerstkaExceptionMiddleware",
]
```

### Django REST Framework

```python
# urls.py
from django.urls import path
from verstka_sdk import LocalStorageAdapter, VerstkaClient, VerstkaConfig
from verstka_sdk.integrations.drf import build_callback_views

client = VerstkaClient(VerstkaConfig(...))
storage = LocalStorageAdapter(root="/srv/media", base_url="https://cdn.example.com")

views = build_callback_views(
    client,
    storage=storage,
    on_content_finalize=on_content_finalize,
)

urlpatterns = [
    path("verstka/callback/", views["callback"].as_view()),
]

# settings.py
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "verstka_sdk.integrations.drf.verstka_exception_handler",
}
```

## Signatures

If you need to sign or verify manually:

```python
from verstka_sdk import sign_material, verify_signature

sig = sign_material(material_id="42", url=callback_url, secret=secret)
ok = verify_signature("42", content_url, signature, secret)
```

Both outgoing `session/open` requests and incoming callbacks use the formula
`hex(HMAC_SHA256(secret, f"{material_id}:{url}"))`. The digest is always sent
in the **`X-Verstka-Signature`** HTTP header (including for incoming
callbacks); the JSON body does not carry a `signature` field.

## Errors

All SDK errors inherit from `VerstkaError`:

- `VerstkaSignatureError` — signature missing or invalid.
- `VerstkaCallbackDataError` — callback payload is malformed.
- `VerstkaApiError` — non-2xx response from Verstka API or content endpoint
  (carries `status_code`).
- `VerstkaContentTooLargeError` — downloaded ZIP exceeds `max_content_size`
  (subclass of `VerstkaApiError`).
- `VerstkaVmsJsonError`, `VerstkaMetadataJsonError` — invalid JSON input.

## Migrating from `verstka_api_v2.py`

| Old service method                       | New API                                                   |
|------------------------------------------|-----------------------------------------------------------|
| `VerstkaV2APIService().get_editor(...)`  | `AsyncVerstkaClient.get_editor_url(...)`                  |
| `process_callback_v2(save_media_fn, finalize_fn)` | `process_material_callback(..., signature=header, storage, on_finalize)` |
| `process_site_fonts_callback(save_font_fn)` | `process_fonts_callback(..., signature=header, storage, on_finalize)`         |
| `verstka_callback_v2_new` decorator      | call `process_material_callback` with ``X-Verstka-Signature`` from your view |
| `VerstkaV2APIError`                      | `VerstkaApiError`                                         |
| `VerstkaV2APIWrongCallbackData`          | `VerstkaSignatureError` / `VerstkaCallbackDataError`      |
| `VerstkaV2MetadataJsonError`             | `VerstkaMetadataJsonError`                                |
| `VerstkaV2VmsJsonError`                  | `VerstkaVmsJsonError`                                     |
| `app.config.get_settings()`              | `VerstkaConfig(...)`                                      |
| `raise HTTPException(...)`               | handled by `install_exception_handlers(app)`              |

The callback response shape is identical:
`{"rc": 1/0, "rm": "...", "data": {...}}`.

## Development

```bash
python3.10 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e '.[dev]'

pytest
ruff check src tests
mypy src
```

The repository `.gitignore` ignores both `venv/` and `.venv/` — use whichever
path you prefer locally.

## Changelog

### 0.1.0

- Initial release. Extracted from in-house `verstka_api_v2.py`.
- Sync + async clients on `httpx`.
- `StorageAdapter` / `AsyncStorageAdapter` protocols with reference
  `LocalStorageAdapter` / `LocalAsyncStorageAdapter` implementations.
- Typed `ContentFinalizeContext` / `FontsFinalizeContext` with
  `saved_media_urls` / `saved_font_urls` fields; `*FinalizeResult` to shape
  the outgoing HTTP response.
- Optional `on_pre_save` hooks (`ContentPreSaveContext` /
  `FontsPreSaveContext` → `PreSaveDecision`) that gate storage writes based
  on `material_id`/`metadata` before any ZIP download.
- Optional `on_fonts_finalize`: integrations always register
  `/fonts-callback`; when the hook is omitted the SDK still persists fonts
  through `storage` and returns the default payload to Verstka.
- Integrations: FastAPI, Flask, Django (async views + middleware), DRF.

## License

MIT — see [LICENSE](LICENSE).
