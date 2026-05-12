# Task F02 — WhatsApp send_image client method and images activity

- Slug: whatsapp-image-send-activity
- HU id: 06
- Target agent: sales_whatsapp
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §4, §12)
- Planner: exoclaw-task-planner-archon
- Date: 2026-05-11
- Iteration: 1
- Estimated LOC: 120
- Risk: medium

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- AC-3: "Given `WHATSAPP_ACCESS_TOKEN` is empty (local dev), Then the activity logs a fake-send and returns success without raising (parity with `whatsapp_client.send_message`)."
- AC-4: "Given a Meta-side transient failure (HTTP 5xx), Then the activity retries per `_IMAGE_SEND_OPTIONS` until exhausted; if exhausted, the workflow logs the failure and **continues the turn** — image-send failure does NOT prevent the text reply from going out."

Refinement sections that informed this task: §4 (Activities), §12 (Tests — activity test).

## 2. Dependencies

- depends_on: ['F01']
- blocks: ['F04']
- Inherits from upstream tasks: F01 introduced `ProductImagePayload` (used as the `images` parameter type in the activity signature) and `_IMAGE_SEND_OPTIONS` (the retry preset the workflow will pass when calling this activity).

## 3. Files affected

All paths are RELATIVE TO REPO ROOT. CWD for verification commands = `hubara_agency/`.

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| hubara_agency/src/platform/whatsapp/client.py | modify | Add `send_image` async function | +22 |
| hubara_agency/src/platform/whatsapp/activities.py | modify | Add `send_whatsapp_images_activity` | +40 |
| hubara_agency/tests/platform/whatsapp/test_send_images_activity.py | new | Activity tests (ActivityEnvironment) | ~58 |

`client.py` and `activities.py` are NOT declared in `.exoclaw/spinal-files.yaml`.
Only F02 modifies them; no parallel batch conflict arises.

The test directory `hubara_agency/tests/platform/whatsapp/` may need to be created
(see §13). The project-context lists `tests/platform/{catalog,medusa,...}` — a `whatsapp/`
subdir may be missing.

## 4. Boundary DTOs (R-JSON)

No new DTOs in this task. Uses `ProductImagePayload` (introduced by F01):

```python
# from src.platform.contracts import ProductImagePayload  (defined in F01)
# Activity signature:
# async def send_whatsapp_images_activity(
#     session_id: str,
#     images: list[ProductImagePayload],
# ) -> None: ...
```

`list[ProductImagePayload]` crosses `execute_activity` — Temporal serializes it via
`dataclass_to_dict` (framework default). R-JSON compliant because `ProductImagePayload`
is a plain dataclass with primitive fields.

## 5. Tools / Activities / Workflow snippets

### `send_image` in `src/platform/whatsapp/client.py`

```python
# canonical — src/platform/whatsapp/client.py (append after send_message)
async def send_image(
    phone_number_id: str, to: str, image_url: str, caption: str | None = None
) -> None:
    """Envía un mensaje type=image a través del WhatsApp Cloud API."""
    if not WHATSAPP_ACCESS_TOKEN:
        logger.warning("Fake Image Send", to=to, image_url=image_url)
        return

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    image_block: dict = {"link": image_url}
    if caption:
        image_block["caption"] = caption
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image_block,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code == 200:
            logger.info("WhatsApp Image OK", to=to, image_url=image_url)
        else:
            response.raise_for_status()  # let Temporal retry on 5xx
```

### `send_whatsapp_images_activity` in `src/platform/whatsapp/activities.py`

```python
# canonical — src/platform/whatsapp/activities.py (append after send_whatsapp_message_activity)
from src.platform.contracts import ProductImagePayload  # new import

@activity.defn(name="send_whatsapp_images_activity")
@with_heartbeat(every=10)
async def send_whatsapp_images_activity(
    session_id: str, images: list[ProductImagePayload]
) -> None:
    from_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")

    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")

    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
    except (OSError, json.JSONDecodeError):
        pass

    for img in images:
        await whatsapp_client.send_image(
            phone_number_id, from_number, img.image_url, img.caption
        )
        await asyncio.sleep(1.5)  # parity with send_whatsapp_message_activity chunking
```

**Fake-send path** (AC-3): when `WHATSAPP_ACCESS_TOKEN` is empty, `client.send_image` logs
a warning and returns without HTTP call — the activity completes normally. This is identical
to the existing `send_message` fake-send pattern.

**Retry path** (AC-4): `raise_for_status()` on HTTP ≥ 400 lets Temporal retry per
`_IMAGE_SEND_OPTIONS` (3 attempts, 2s initial interval). When all attempts are exhausted,
Temporal raises `ActivityError`; the workflow (F04) catches it and continues the turn.

## 6. Workspace changes

None — this task is platform-layer only. No workspace markdown files touched.

## 7. Composition wiring

No new factories in `composition.py`. The activity is a side-effect function; it does not
need a factory. The catalog client used in the tool (F03) is a separate concern.

## 8. Worker registration

No worker registration in this task. The `send_whatsapp_images_activity` is registered in
`src/sales_whatsapp/worker.py` by F04 (after the workflow branch that calls it is in place).

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| tests/platform/whatsapp/test_send_images_activity.py | new | fake-send (empty token), real send (mocked httpx), heartbeat fires |

Test name list:
- `test_send_images_activity_fake_send_when_token_empty` — `WHATSAPP_ACCESS_TOKEN=""`, run via
  `ActivityEnvironment`, assert no exception raised, assert logger.warning called for each image.
- `test_send_images_activity_posts_one_request_per_image` — token set, `respx` (or
  `unittest.mock.patch` on `whatsapp_client.send_image`), assert `send_image` called once per
  `ProductImagePayload` in `images`, assert `asyncio.sleep(1.5)` called between calls.
- `test_send_images_activity_raises_on_exhausted_retries` — `send_image` patched to raise
  `httpx.HTTPStatusError` (5xx), assert activity raises (Temporal will retry; test just
  verifies the propagation).
- `test_send_images_activity_heartbeat_fires` — run with `ActivityEnvironment` and verify
  `@with_heartbeat(every=10)` is wired (check decorator presence or mock heartbeat call).

## 10. Verification commands

```bash
cd hubara_agency && uv run pytest tests/platform/whatsapp/test_send_images_activity.py -xvs
cd hubara_agency && uv run ruff check src/platform/whatsapp/client.py src/platform/whatsapp/activities.py
cd hubara_agency && uv run mypy src/platform/whatsapp/client.py src/platform/whatsapp/activities.py
cd hubara_agency && uv run pytest tests/ -x -q --ignore=tests/integration
```

## 11. Definition of Done

- [ ] `send_image` function added to `hubara_agency/src/platform/whatsapp/client.py` with fake-send path.
- [ ] `send_whatsapp_images_activity` added to `hubara_agency/src/platform/whatsapp/activities.py` with `@activity.defn(name="send_whatsapp_images_activity")` and `@with_heartbeat(every=10)`.
- [ ] Activity loops over `images`, calls `whatsapp_client.send_image`, sleeps 1.5s between calls.
- [ ] All 4 test scenarios in `test_send_images_activity.py` implemented and passing.
- [ ] All verification commands in §10 exit 0.
- [ ] No regression in existing test suite.
- [ ] R-rules check in §12 confirmed.

## 12. R-rules check

- R-DET: **not applicable** — no workflow code in this task. The activity is a side-effect
  function; Temporal enforces determinism at the workflow layer.
- R-JSON: **applies** — activity input `list[ProductImagePayload]` is a list of plain
  dataclasses (defined in F01). Crosses `execute_activity` boundary cleanly.
- R-STATELESS: **applies** — activity rebuilds `httpx.AsyncClient` per call inside `async with`.
  Reads `WHATSAPP_ACCESS_TOKEN` from `config.py` module constant (resolved once at import time,
  same pattern as existing `send_message`). No module-level `_client = ` state. ✓
- R-HEARTBEAT: **applies** — `@with_heartbeat(every=10)` wraps the activity (multi-image
  POSTs can exceed 10s total). Heartbeat timeout set to 30s in `_IMAGE_SEND_OPTIONS`. ✓
- R-DIP: **applies** — `activities.py` imports `whatsapp_client` (pure HTTP module, no
  Temporal) via the existing import `from src.platform.whatsapp import client as whatsapp_client`.
  No import of `temporalio.client` inside the activity. ✓

## 13. Open questions / risks

- **`tests/platform/whatsapp/` directory**: may not exist. Implementer must create it with
  `__init__.py` (or check if tests/platform uses implicit namespace packages — check existing
  `tests/platform/catalog/` to see if `__init__.py` files are present).
- **`raise_for_status()` vs explicit status check**: `send_message` uses an explicit
  `if response.status_code == 200 / else logger.error` pattern (does not raise). For
  `send_image`, the refinement requires Temporal retries on 5xx — so `raise_for_status()` is
  needed. This is a deliberate divergence from the existing client pattern. Flag in PR.
- **`asyncio.sleep(1.5)` inside activity**: this is fine inside activities (non-determinism
  rules apply only inside `@workflow.run`). The sleep matches the existing text-message
  chunking interval at `activities.py:40`.
- **`@with_heartbeat` ordering**: verify that `@activity.defn` goes ABOVE `@with_heartbeat`
  (same stacking order as `send_whatsapp_message_activity` at `activities.py:20-21`).
