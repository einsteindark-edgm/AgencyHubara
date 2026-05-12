# Tech refinement — 06 Send product images during sales conversation

- **HU id**: 06 (no formal id provided — assigned by convention; the file slot is free)
- **Source**: (inline) "Quiero que el agente de ventas envíe al cliente las imágenes de los productos en los que está interesado, durante la conversación de ventas."
- **Target agent**: `sales_whatsapp` (cwd: `/Users/edgm/Documents/Projects/AgencyHubara/`)
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-11

## 0. Locating the agent and how it knows the products of interest

- The agent lives at `hubara_agency/src/sales_whatsapp/` (multi-agent uv workspace under `hubara_agency/`, sibling agents `remarketing_whatsapp/` and `catalog_sync/`; cross-agent infra at `hubara_agency/src/platform/`). DEHA layout confirmed (`workflows/`, `activities/`, `tools/`, `use_cases/`, `workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md`, `composition.py`, `contracts.py`, `state.py`, `worker.py`).
- **How the agent knows the products of interest**: the LLM does **not** receive products via an entity-extraction step. The closed-list grounding works as follows (`workspace/TOOLS.md:43-56`):
  1. The user asks about products; the LLM invokes `search_products` (`src/sales_whatsapp/tools/catalog.py:29`) or `get_product_by_handle` (`src/sales_whatsapp/tools/catalog.py:117`).
  2. Each tool returns a JSON envelope keyed by `handle` whose `results[*]` (search) or `product` (detail) include `thumbnail_url`, `images: [{url, rank}]`, `title`, `price`, etc. (DTOs at `src/platform/catalog/dtos.py:28-44`).
  3. The system prompt obliges the LLM to only cite products by `handle` already returned in a recent `tool_result` (`workspace/TOOLS.md:44`). Therefore "products of interest" at any point in the conversation is the **handle set** the LLM has just seen.
- **Consequence for this HU**: the new "send images" capability is best modeled as a **tool the LLM explicitly invokes** with the handle(s) it just confirmed are of interest. We do NOT introduce a separate intent-classifier; we reuse the catalog's closed-list semantics. The LLM is already trained to operate over handles via TOOLS.md guidance.

## 1. Scope

**Summary**: Add a new sales-domain tool `send_product_images` that, given a list of product `handle`s the LLM has just confirmed are of interest, dispatches the corresponding product images to the customer via WhatsApp Cloud API during the conversation. The tool emits a **`send_images_decision` envelope** (ADR-001 pattern, parallel to `transfer_decision` / `schedule_remarketing`); the workflow consumes the decision and calls a new dedicated activity `send_whatsapp_images_activity` that POSTs `type=image` messages to Meta. Image URLs come from the existing `CatalogPort` snapshot — no new image storage.

**Acceptance criteria**:
- Given a customer asks "¿me puedes mandar fotos?" and the LLM has already called `search_products` for the relevant product, When the LLM invokes `send_product_images(handles=["luz-serena"], caption_per_image="Vela Luz Serena")`, Then the customer receives one WhatsApp `type=image` message per image of the resolved product (using `thumbnail` + extra `images[*].url` from the snapshot, capped by `max_images_per_handle` default 3), each with optional caption.
- Given the LLM invokes `send_product_images(handles=["non-existent"])`, Then the tool returns `{"status": "partial", "sent": [], "skipped": [{"handle": "non-existent", "reason": "not_found"}]}` and **never crashes** (catalog miss is normal input to the LLM).
- Given `WHATSAPP_ACCESS_TOKEN` is empty (local dev), Then the activity logs a fake-send and returns success without raising (parity with `whatsapp_client.send_message` at `src/platform/whatsapp/client.py:18-20`).
- Given a Meta-side transient failure (HTTP 5xx), Then the activity retries per `_IMAGE_SEND_OPTIONS` (see §4) until exhausted; if exhausted, the workflow logs the failure and **continues the turn** — image-send failure does NOT prevent the text reply from going out (degraded UX > hung session).
- Given the LLM invokes `send_product_images` with `handles=[]` or with > 5 handles, Then the tool returns `{"status": "error", "message": "..."}` enforced by JSON schema constraints (`minItems: 1`, `maxItems: 5`).
- `workspace/TOOLS.md` documents when to use / not use the tool, and explicit rules (e.g. "do not send images speculatively; only when the customer asked or you offered and they accepted").
- Replay fixture `tests/fixtures/history_sales_session_v3.json` exists (bumped from v2) because the workflow loop now reads a new decision field; old fixture would not replay deterministically against the new signature.

**Out of scope**:
- Sending **video** or **audio** attachments (only `type=image` in v1).
- Sending **interactive messages** / catalog templates / WhatsApp product carousels.
- Uploading binaries to Meta `/media` endpoint (URL-based send only in v1 — see §13).
- Caching image binaries locally; the catalog snapshot URLs are the source of truth.
- Letting the Remarketing agent send images (only Sales for now; can be promoted to `platform/` later).
- Changing the inbound `PendingMessage.media` semantics (those flow user → agent; this HU is agent → user).

## 2. Workflow mode

**Decision**: **Extending existing `HubaraSalesSessionWorkflow`** at `src/sales_whatsapp/workflows/sales_session.py:29-141` (session_based, signal-driven). One new branch inside the existing per-turn dispatch block at `sales_session.py:101-114`: after parsing `result.send_images_decision`, call `send_whatsapp_images_activity` before the existing `send_whatsapp_message_activity` text-reply.

**Justification**: The HU is a per-turn side effect emitted by an LLM tool, identical structurally to `transfer_decision` / `schedule_remarketing`. A new workflow would force a parallel session and serialize image-sends out of band — strictly worse than keeping them inline with the current text reply.

**File**: `src/sales_whatsapp/workflows/sales_session.py` (existing — additive change).

## 3. Boundary DTOs (R-JSON)

| DTO | File | Fields | Notes |
|---|---|---|---|
| `SendProductImagesDecision` | `src/platform/contracts.py` | `session_id: str`, `images: list[ProductImagePayload]` | `frozen=True`; cross-agent slot (mirrors `TransferDecision`/`ScheduleRemarketingDecision`). Placed in `platform/contracts.py` so the dispatcher activity (if ever moved) and `workflow_helpers.run_agent_turn` parser can both import it without back-coupling to `sales_whatsapp`. |
| `ProductImagePayload` | `src/platform/contracts.py` | `handle: str`, `image_url: str`, `caption: str \| None = None`, `rank: int = 0` | Plain dataclass, JSON-serializable. `image_url` is **a public HTTPS URL** (Meta requires `type=image.link` to be HTTPS, ≤ 5 MB). |

**Why platform/contracts.py vs sales_whatsapp/contracts.py**: the existing decision DTOs already live in `src/platform/contracts.py` (`TransferDecision`, `ScheduleRemarketingDecision`). Keeping `SendProductImagesDecision` next to them preserves the convention and lets `workflow_helpers._try_parse_decision_payload` (`src/platform/workflow_helpers.py:82-97`) and `run_agent_turn` (same file, lines 100-225) extend without changing import direction.

**Mutation to `TurnResult`** (in `src/platform/workflow_helpers.py:68-79`): add `send_images_decision: SendProductImagesDecision | None = None`. `TurnResult` is a workflow-internal dataclass (not crossing `execute_activity`), so it's tolerated as a non-frozen `@dataclass` like its siblings.

**Reused from `exoclaw_temporal.config`**: `SessionInput`, `BuildPromptInput`, `LLMChatInput`, `ExecuteToolInput`, `RecordTurnInput`, `WorkspaceConfig`. No re-declaration.

## 4. Activities

| Activity | File | Input → Output | Retry preset | Heartbeat | Use case invoked | Notes |
|---|---|---|---|---|---|---|
| `send_whatsapp_images_activity` | `src/platform/whatsapp/activities.py` (existing file, new function) | `(session_id: str, images: list[ProductImagePayload]) → None` | new constant `_IMAGE_SEND_OPTIONS` (see below) | **Yes**: `@with_heartbeat(every=10)` — Meta may take >10s on multi-image bursts | No use case (single-purpose side effect) | One HTTP POST per image. Each call uses the same `phone_number_id` resolution as `send_whatsapp_message_activity` (env or metadata.json override). Logs per-image OK/fail. On exhausted retries, **raises**; the workflow catches and continues (see §2). |

**New retry preset** in `src/platform/temporal/retry_policies.py` (existing file, additive):

```python
# pseudo
_IMAGE_SEND_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=3),  # multi-image bursts (≤5 images × ~30s p99)
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
}
```

**Why a new preset**: `_CONV_OPTIONS` (2 min, 5 attempts) is too aggressive (rate-limited Meta sends would burn the attempt budget); `_LLM_OPTIONS` (5 min, 3 attempts) is closer but image sends are HTTP, not LLM, so a smaller window is appropriate. Inline justification will go into the constant's docstring.

**R-STATELESS check**: the activity reads `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` via `os.getenv(...)` and rebuilds an `httpx.AsyncClient` per call (same as `send_whatsapp_message_activity` at `src/platform/whatsapp/activities.py:25-40`). No module-level state. ✓

**Pre-existing R-rule conformance note**: `send_whatsapp_message_activity` already heartbeats every 10s (`platform/whatsapp/activities.py:21`) and uses async `httpx`. The new function follows the same template. No deviations.

## 5. Tools

| Tool class | File | LLM name | Parameters (JSON schema, summarized) | Side effects | Workspace TOOLS.md change |
|---|---|---|---|---|---|
| `SendProductImagesTool` | `src/sales_whatsapp/tools/images.py` (new file) | `send_product_images` | `{type: "object", properties: {handles: {type: "array", items: {type: "string", minLength: 1, maxLength: 200}, minItems: 1, maxItems: 5, description: "Lista de handles del catálogo (vistos en search_products o get_product_by_handle) cuyas imágenes quieres enviar."}, caption_per_handle: {type: "object", additionalProperties: {type: "string", maxLength: 500}, description: "(opcional) caption por handle. Si falta, se envía sin caption."}, max_images_per_handle: {type: "integer", minimum: 1, maximum: 5, default: 3, description: "Cuántas imágenes enviar por producto (cap, default 3, máx 5)."}}, required: ["handles"]}` | Lee del `CatalogPort` (rebuild factory). Emite `send_images_decision` en el envelope JSON. **No** ejecuta el envío HTTP directamente (R-DIP: la tool no importa Temporal client ni `whatsapp_client`). | Nueva sección "### `send_product_images`" en `workspace/TOOLS.md` con cuándo usar / cuándo NO usar, regla de "solo si el cliente lo pidió o aceptó tu oferta", regla de citar el `handle` literal del último `tool_result`, regla de **no usar la tool sin haber confirmado el handle vía search_products primero**. |

Signature:

```python
# pseudo
async def execute_with_context(
    self,
    ctx: ToolContext,
    handles: list[str],
    caption_per_handle: dict[str, str] | None = None,
    max_images_per_handle: int = 3,
) -> str: ...
```

Return envelope shape (the workflow parses `send_images_decision`):

```jsonc
// pseudo
{
  "status": "ok" | "partial" | "error",
  "sent": [
    {"handle": "luz-serena", "image_count": 3}
  ],
  "skipped": [
    {"handle": "non-existent", "reason": "not_found"}
  ],
  "send_images_decision": {
    "session_id": "wa_573125671604",
    "images": [
      {"handle": "luz-serena", "image_url": "https://cdn.hubara.../1.jpg", "caption": "...", "rank": 0}
    ]
  },
  "message": "Te envío las fotos en breve."
}
```

**Tool body responsibilities** (DEHA-aligned):
1. Resolve handles via `CatalogPort.get_by_handle` (one call per handle; missing handles → `skipped`).
2. For each found product: take `thumbnail` (if present) + `images[*].url` sorted by `rank`, truncate to `max_images_per_handle`. Pair with caption from `caption_per_handle.get(handle)` (only the **first** image of a handle carries the caption, the rest are captionless — convention to avoid spam).
3. Emit a single `send_images_decision` envelope; the workflow loop reads it and dispatches `send_whatsapp_images_activity` exactly once per turn (so 1 LLM tool call → 1 activity invocation → N HTTP POSTs).
4. The tool itself never imports `temporalio.client`, never imports `src.platform.whatsapp.*`, and never reads `os.environ`. ✓ R-DIP.

## 6. Use cases (optional)

**No use case needed** — the coordination logic (resolve handles → build payload → emit decision) is ~15 lines, lives entirely inside the tool body, and is not reused outside this tool. If a future HU adds "send images on workflow timeout" we'll extract a use case then.

## 7. State adapters

**No new adapter**. Catalog reads route through the existing `CatalogPort` (`src/platform/catalog/port.py`) and `LocalSnapshotCatalogClient` (`src/platform/catalog/local_snapshot.py`). No new persistent state — image URLs are not cached; they're read fresh from the (already-cached) snapshot on every send.

## 8. Prompts / workspace changes

- `src/sales_whatsapp/prompts.py` — **no change** (this HU does not introduce a workflow-emitted prompt; the ghosting prompt is untouched).
- `src/sales_whatsapp/workspace/IDENTITY.md` — no change.
- `src/sales_whatsapp/workspace/SOUL.md` — **small delta**: one bullet under tone guidance like _"cuando ofrezcas mandar fotos, hazlo natural ('te mando fotos en un momento') y nunca digas 'cargando' ni hagas teatro técnico"_. (Optional — recommended to keep the LLM from leaking technical jargon.)
- `src/sales_whatsapp/workspace/USER.md` — no change.
- `src/sales_whatsapp/workspace/TOOLS.md` — **new section** between `### get_product_by_handle` and `## Reglas anti-alucinación` (`workspace/TOOLS.md:36-45`). Content outline:
  - **Use when**: el cliente pide explícitamente ver fotos del producto ("mándame fotos", "tienes imagen", "¿cómo se ve?"); o el cliente eligió un producto y vas a ofrecerle mostrarlo antes de cerrar.
  - **Don't use when**: el cliente aún no ha mostrado interés concreto en el producto; el cliente está en el cierre y ya tiene los datos; cuando aún no llamaste `search_products` o `get_product_by_handle` para confirmar el handle.
  - **Required context**: lista de handles confirmados en el último `tool_result`.
  - **Side effects**: envía mensajes `type=image` por WhatsApp al cliente. Hasta 3 imágenes por producto, hasta 5 productos por llamada.
  - **Output**: `{status, sent: [...], skipped: [...], message}`. Si `status == "partial"`, comunica al cliente naturalmente ("para el otro producto no tengo foto a mano, te lo describo").
  - **Regla anti-spam**: máximo una invocación de `send_product_images` por turno. Si fallan envíos, NO retries con la misma tool en el siguiente turno — describe el producto con texto.
- `src/sales_whatsapp/workspace/AGENTS.md` — no change.
- `src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md` — no change (skill is identity/policies; images are not a policy).
- **No new skill**. Sending images is a primary capability, not a loadable one — it belongs in `TOOLS.md`, always available.

> Frontmatter rule reminder: any future skill must use single-line inline JSON for `metadata`.

## 9. Composition wiring

| Factory in `composition.py` | Returns | Consumed by |
|---|---|---|
| **No new factory** in `src/sales_whatsapp/composition.py` — the catalog client singleton `_catalog = get_catalog_client()` already exists at `src/sales_whatsapp/worker.py:49` and is closure-captured by the new tool's `register_tool_extension(...)` lambda (identical pattern to `SearchProductsTool` / `GetProductByHandleTool` at `worker.py:51-58`). | `CatalogPort` instance | `SendProductImagesTool.__init__` (`workspace`, `catalog`) |

If a future HU needs a per-session image-send rate limiter, that's when a dedicated factory in `composition.py` is justified.

## 10. Worker registration (`worker.py`)

- Add to `workflows=[...]`: **no change** (workflow class unchanged).
- Add to `activities=[...]`: **`send_whatsapp_images_activity`** at `src/sales_whatsapp/worker.py:70-81`.
- `register_tool_extension(...)` — new entry at `src/sales_whatsapp/worker.py` after the existing `sales.get_product_by_handle` registration:
  ```python
  # pseudo
  register_tool_extension(
      "sales.send_product_images",
      lambda workspace: SendProductImagesTool(workspace=str(workspace), catalog=_catalog),
  )
  ```
- New import: `from src.sales_whatsapp.tools.images import SendProductImagesTool` and `from src.platform.whatsapp.activities import send_whatsapp_images_activity` (existing module — extend its imports list in `worker.py:12`).

## 11. Hard rules check

- **R-DET**: applies — handled by extending the workflow with a deterministic branch (read `result.send_images_decision`, conditionally `await workflow.execute_activity(send_whatsapp_images_activity, ...)`). Zero `time.time()`, `random`, `os.environ` in the workflow file. Env reads (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`) stay inside the activity, mirroring `send_whatsapp_message_activity`.
- **R-JSON**: applies — `SendProductImagesDecision` and `ProductImagePayload` are plain frozen dataclasses with `str`/`int`/`list[...]`/`str | None` fields. They cross `workflow.execute_activity` cleanly. The tool returns `str` (JSON) as required by `ToolBase.execute_with_context`.
- **R-STATELESS**: applies — the new activity rebuilds `httpx.AsyncClient` per call; the new tool receives `CatalogPort` via constructor (closure-captured singleton in `worker.py`, same pattern as existing catalog tools). No module-level `_X = ` introduced.
- **R-HEARTBEAT**: applies — `send_whatsapp_images_activity` wraps `@with_heartbeat(every=10)` (multi-image POSTs may exceed 10s in p99). Inherits the framework's `with_heartbeat` from `src/platform/temporal/heartbeat.py`.
- **R-DIP**: applies — workflow imports stay clean (only `with workflow.unsafe.imports_passed_through()` for the new activity and DTO). Tool does NOT import `temporalio.client` or `src.platform.whatsapp.*`. `contracts.py` (platform) imports only `dataclasses`.

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/sales_whatsapp/tools/test_send_product_images_envelope.py` | Tool (unit) | Given a fake `CatalogPort` with two known handles and one unknown, the tool returns a JSON envelope where `status="partial"`, `sent` has 2 entries, `skipped` has 1, and `send_images_decision.images` length equals sum of per-handle image counts capped by `max_images_per_handle`. |
| `tests/sales_whatsapp/tools/test_send_product_images_protocol.py` | Tool (protocol) | `ToolBase` schema validation: empty `handles`, `>5` handles, non-list `handles` → `Error: Invalid parameters ...` returned to LLM. Asserts the tool conforms to exoclaw `Tool` Protocol via `runtime_checkable` check. |
| `tests/sales_whatsapp/tools/test_send_product_images_unavailable.py` | Tool (unit) | Fake catalog raises `CatalogUnavailableError` → envelope `{"status": "error", "message": "catálogo no disponible..."}`, decision absent. |
| `tests/platform/whatsapp/test_send_images_activity.py` | Activity | `temporalio.testing.ActivityEnvironment`. With `WHATSAPP_ACCESS_TOKEN=""`, runs N "fake sends" without raising. With token set + httpx mocked (`respx` or monkeypatched `whatsapp_client.send_image`), asserts one POST per image with `type=image, image.link=<url>`. Asserts heartbeat fires (use `ActivityEnvironment.heartbeat_details`). |
| `tests/sales_whatsapp/workflows/test_sales_session_images_branch.py` (new file, or extend `test_run_agent_turn.py`) | Workflow (integration) | `WorkflowEnvironment.start_time_skipping`. Inject a fake `execute_tool` that returns the new envelope with a non-null `send_images_decision`. Assert `send_whatsapp_images_activity` was invoked exactly once with the expected payload, then `send_whatsapp_message_activity` was invoked for the LLM text reply. |
| `tests/test_replay_sales.py` | Replay | Bump fixture from `history_sales_session_v2.json` to `history_sales_session_v3.json` because the workflow path now branches on a new `TurnResult` field. Regenerate via `tests/fixtures/generate_fixtures.py` with the new code path exercised. |
| `tests/sales_whatsapp/workspace/test_workspace_system_prompt.py` | Workspace | Assert that the composed system prompt for a Sales session contains the new TOOLS.md bullets for `send_product_images` (existing test pattern — just add the asserted strings). |
| `tests/test_imports.py` | Smoke | Cover the new module paths (`src.sales_whatsapp.tools.images`, `src.platform.contracts.SendProductImagesDecision`, `src.platform.whatsapp.activities.send_whatsapp_images_activity`) — keeps boundary imports honest. |

**Replay**: bump fixture to `history_sales_session_v3.json`. **Reason**: `TurnResult` gains a new optional field (`send_images_decision`) and the workflow branches on it. A v2 history that does NOT include the new branch will still replay green for the no-images path, but new fixtures must capture the branched path. Generate by running a session that exercises `send_product_images` once.

## 13. Risks / open questions

1. **URLs vs binary upload** — *open architectural decision.*
   - **Recommended default**: send images by `image.link` (URL) in v1. The catalog snapshot already stores Medusa public CDN URLs (`CatalogImageDTO.url`, see `src/platform/catalog/dtos.py:28-31`). Meta's WhatsApp Cloud API requires URLs to be public HTTPS and ≤ 5 MB; the Medusa CDN already serves them this way.
   - **Risk**: if Medusa-served URLs are signed and short-lived, Meta may refuse them. **Mitigation**: verify in §14 step 1 that `LocalSnapshotCatalogClient.search` returns URLs that are accessible without auth. If not, we need a follow-up HU to introduce `MediaUploadActivity` (POST `/<phone-number-id>/media`, get `media_id`, then `type=image.id`).
2. **Multi-image vs carousel** — *open architectural decision.*
   - **Recommended default**: send **N independent `type=image` messages** (1 HTTP POST per image, sleep 1.5s between calls — parity with text chunking at `src/platform/whatsapp/activities.py:37-40`). WhatsApp does not support a native carousel for free-form conversations; carousels require pre-approved **`template`** messages (a different API path, with Business approval).
   - **Cap**: 3 images per handle (`max_images_per_handle` default), 5 handles per call (`maxItems`). Worst case = 15 image POSTs in a turn. With 1.5s sleep + retries, that fits inside `_IMAGE_SEND_OPTIONS.start_to_close_timeout=3min` comfortably; if not, raise to 5min.
3. **Image caching** — *open architectural decision.*
   - **Recommended default**: no local cache. Meta fetches the URL once and caches on their side (using `media_id` reuse would only help if we move to upload-mode). For URL-mode, repeated sends of the same image are idempotent at our end.
   - **Risk**: if URLs change per Medusa publish, the LLM might cite the same `image_url` twice in different turns and the second send may succeed/fail unpredictably. **Mitigation**: tool always re-resolves URLs via `CatalogPort.get_by_handle` per call; never the LLM passes URLs directly.
4. **What "products of interest" means at the tool layer** — the LLM decides which handles to pass; we do not extract intent. If product safety / over-share matters, we'd add a workflow-level guard (e.g. dedup sends per session); deferred.
5. **Cross-agent reusability of `send_whatsapp_images_activity`** — placed in `src/platform/whatsapp/activities.py` (next to `send_whatsapp_message_activity`) so future Remarketing can call it without duplication. This HU only wires it into the Sales worker.
6. **Phone number ID resolution** — the activity replicates the same env-with-metadata-override pattern as `send_whatsapp_message_activity` at `src/platform/whatsapp/activities.py:25-35`. Identical bug surface; no new risk.
7. **Existing `PendingMessage.media`** (`src/platform/workflow_helpers.py:63`) — this field is for **inbound** media (user → agent). This HU does NOT touch it; it would be confusing to repurpose it for outbound. Flag for the implementer: do not conflate inbound media with outbound images.
8. **Pre-existing R-rule violations** — none directly in the touched files. `bootstrap_session.py:84` has a stale comment referencing `src/domains/sales_whatsapp/` (legacy `domains/` layout that was migrated to flat `src/<agent>/`); not strictly an R-rule break, but worth a one-liner cleanup at the implementer's discretion (out of scope).
9. **Defer to `temporal:temporal-developer`**: **none** — no child workflows, no saga, no versioning. Standard activity-from-workflow dispatch.
10. **Defer to `claude-api`**: **none** — no Claude/Anthropic-specific behavior; image send is a downstream HTTP concern, LLM-agnostic.

## 14. Implementation order (suggested)

1. **Verify Medusa image URLs are publicly fetchable** (curl one of the URLs returned by `search_products` from an external network). If not, stop and open a follow-up HU for upload-mode. **No code yet.**
2. **DTOs first**: add `ProductImagePayload` + `SendProductImagesDecision` to `src/platform/contracts.py`. Run `cd hubara_agency && uv run pytest tests/test_remarketing_contract.py -xvs` to confirm no regression on existing decision parsing.
3. **Workflow helpers**: extend `TurnResult` with `send_images_decision: SendProductImagesDecision | None = None` in `src/platform/workflow_helpers.py:68-79`; extend `_try_parse_decision_payload` consumer in `run_agent_turn` (`workflow_helpers.py:174-191`) to also populate the new field. Re-run `tests/test_run_agent_turn.py`.
4. **Retry policy**: add `_IMAGE_SEND_OPTIONS` to `src/platform/temporal/retry_policies.py` with the documented constants.
5. **WhatsApp client**: add `async def send_image(phone_number_id, to, image_url, caption)` to `src/platform/whatsapp/client.py` (sibling of `send_message` at lines 16-40). Same `httpx.AsyncClient` pattern; body uses `"type": "image", "image": {"link": image_url, "caption": caption}`.
6. **Activity**: add `send_whatsapp_images_activity` to `src/platform/whatsapp/activities.py` next to `send_whatsapp_message_activity`. `@activity.defn(name="send_whatsapp_images_activity") @with_heartbeat(every=10)`. Loop over `images`, call `whatsapp_client.send_image`, sleep 1.5s between calls.
7. **Tool**: create `src/sales_whatsapp/tools/images.py` with `SendProductImagesTool` per §5. Resolve handles via `CatalogPort`; build the envelope + decision; return JSON string.
8. **Worker wiring**: in `src/sales_whatsapp/worker.py` — add `register_tool_extension("sales.send_product_images", ...)` and append `send_whatsapp_images_activity` to the `activities=[...]` list. Add imports.
9. **Workflow**: in `src/sales_whatsapp/workflows/sales_session.py` — inside the existing per-turn block at lines 100-114, after the `transfer_decision` branch, add a `result.send_images_decision is not None` branch that `await workflow.execute_activity(send_whatsapp_images_activity, args=[session.session_id, result.send_images_decision.images], **_IMAGE_SEND_OPTIONS)`. Wrap with `try/except` so an exhausted-retry exception does not abort the text reply (log + continue).
10. **Workspace**: append the new TOOLS.md section per §8.
11. **Tests**: write the suite per §12, in this order: tool envelope → tool protocol → tool unavailable → activity → workflow → workspace → smoke imports. Run after each.
12. **Replay fixture**: regenerate `tests/fixtures/history_sales_session_v3.json` via `tests/fixtures/generate_fixtures.py`, point `tests/test_replay_sales.py` at it. Confirm green.
13. **Smoke `uv run pytest -q`** at `cd hubara_agency` to confirm whole suite still passes.
14. **Manual local smoke** (if dev WhatsApp credentials are at hand): start the worker, signal a session with a "mándame fotos" turn after a `search_products` confirmation, verify Meta image messages arrive.

Each step keeps tests green; no Big Bang. The implementer skill will turn this into PRs.

---

**Next step**: invoke the implementer with this file:

```
/exoclaw-implementer .exoclaw/refinements/06-send-product-images-tech.md
```
