# Task F03 — SendProductImagesTool, worker tool extension, workspace TOOLS.md + SOUL.md

- Slug: send-product-images-tool
- HU id: 06
- Target agent: sales_whatsapp
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §5, §8, §10, §12)
- Planner: exoclaw-task-planner-archon
- Date: 2026-05-11
- Iteration: 1
- Estimated LOC: 225
- Risk: low

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- AC-2: "Given the LLM invokes `send_product_images(handles=["non-existent"])`, Then the tool returns `{status: partial, sent: [], skipped: [{handle: non-existent, reason: not_found}]}` and never crashes."
- AC-5: "Given the LLM invokes `send_product_images` with `handles=[]` or with > 5 handles, Then the tool returns `{status: error, message: ...}` enforced by JSON schema constraints."
- AC-6: "`workspace/TOOLS.md` documents when to use / not use the tool, and explicit rules."

Refinement sections that informed this task: §5 (Tools — full spec), §8 (Workspace changes), §10 (Worker registration), §12 (Tests — tool envelope, protocol, unavailable, workspace).

## 2. Dependencies

- depends_on: ['F01']
- blocks: ['F04']
- Inherits from upstream tasks: F01 introduced `ProductImagePayload` and `SendProductImagesDecision` DTOs — the tool builds and returns them inside the decision envelope.

## 3. Files affected

All paths are RELATIVE TO REPO ROOT. CWD for verification commands = `hubara_agency/`.

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| hubara_agency/src/sales_whatsapp/tools/images.py | new | SendProductImagesTool | ~100 |
| hubara_agency/src/sales_whatsapp/worker.py | modify (spinal) | register_tool_extension for send_product_images | +8 |
| hubara_agency/src/sales_whatsapp/workspace/TOOLS.md | modify (spinal) | New `### send_product_images` section | +25 |
| hubara_agency/src/sales_whatsapp/workspace/SOUL.md | modify (spinal) | One bullet on natural image-offer tone | +3 |
| hubara_agency/tests/sales_whatsapp/tools/test_send_product_images_envelope.py | new | Tool unit — partial status, skipped, decision shape | ~32 |
| hubara_agency/tests/sales_whatsapp/tools/test_send_product_images_protocol.py | new | Tool protocol — schema validation, ToolBase conformance | ~28 |
| hubara_agency/tests/sales_whatsapp/tools/test_send_product_images_unavailable.py | new | Tool unit — CatalogUnavailableError handling | ~18 |
| hubara_agency/tests/sales_whatsapp/workspace/test_workspace_system_prompt.py | modify | Assert TOOLS.md bullet present in composed prompt | +11 |

`worker.py`, `TOOLS.md`, `SOUL.md` are declared SPINAL in `.exoclaw/spinal-files.yaml` —
the merger handles them if F03 runs in parallel with another task in B2. Only F02 is in
B2 alongside F03, and F02 does NOT touch these files, so no spinal contention in B2.

`test_workspace_system_prompt.py` is NOT spinal; only F03 modifies it — no conflict.

## 4. Boundary DTOs (R-JSON)

Uses DTOs from F01. No new DTOs in this task.

```python
# from src.platform.contracts import ProductImagePayload, SendProductImagesDecision
# Tool builds these and embeds them in the JSON envelope it returns.
```

## 5. Tool snippet

```python
# canonical — src/sales_whatsapp/tools/images.py (new file)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.catalog import CatalogPort, CatalogUnavailableError, ProductNotFoundError
from src.platform.contracts import ProductImagePayload, SendProductImagesDecision


class SendProductImagesTool(ToolBase):
    name = "send_product_images"
    description = (
        "Envía al cliente las imágenes de uno o más productos del catálogo cuyo "
        "handle ya fue confirmado en un tool_result previo (search_products o "
        "get_product_by_handle). Usa SOLO cuando el cliente pidió fotos "
        "explícitamente o aceptó tu oferta."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handles": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
                "minItems": 1,
                "maxItems": 5,
                "description": "Handles del catálogo cuyas imágenes enviar.",
            },
            "caption_per_handle": {
                "type": "object",
                "additionalProperties": {"type": "string", "maxLength": 500},
                "description": "(opcional) caption por handle. Primera imagen del handle.",
            },
            "max_images_per_handle": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
            },
        },
        "required": ["handles"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        handles: list[str],
        caption_per_handle: dict[str, str] | None = None,
        max_images_per_handle: int = 3,
    ) -> str:
        ...  # implementation: resolve handles, build images list, return JSON envelope
```

**Tool body responsibilities** (from refinement §5):
1. For each handle: call `self._catalog.get_by_handle(handle)`.
   - `ProductNotFoundError` → append to `skipped` with `reason: "not_found"`.
   - `CatalogUnavailableError` → return `{"status": "error", "message": "catálogo no disponible..."}` immediately (no decision field).
2. For each found product: take `thumbnail_url` (if present) as rank-0 image + `images[*]` sorted by rank, truncate to `max_images_per_handle`. Only the first image of a handle carries the caption from `caption_per_handle.get(handle)`.
3. Build `SendProductImagesDecision(session_id=ctx.session_key, images=[...])`.
4. Return JSON string with keys `status`, `sent`, `skipped`, `send_images_decision`, `message`.
5. Never import `temporalio.client` or `src.platform.whatsapp.*` (R-DIP). ✓

**Return envelope** (from refinement §5):
```jsonc
{
  "status": "ok" | "partial" | "error",
  "sent": [{"handle": "...", "image_count": N}],
  "skipped": [{"handle": "...", "reason": "not_found"}],
  "send_images_decision": { "session_id": "...", "images": [...] },
  "message": "Te envío las fotos en breve."
}
```
- `status="ok"`: all handles resolved, images list non-empty.
- `status="partial"`: some handles resolved, some skipped.
- `status="error"`: `handles=[]` (schema blocks this before execute_with_context), `>5` handles (schema blocks), or `CatalogUnavailableError` (runtime error path).
- `send_images_decision` is ABSENT when `status="error"`.

**Schema enforcement** (AC-5): `minItems: 1` and `maxItems: 5` in `parameters` JSON schema.
`ToolBase` validates parameters before calling `execute_with_context` — empty or oversized
`handles` arrays are rejected at the framework layer and returned to the LLM as an error
envelope (existing `ToolBase` protocol behavior).

## 6. Workspace changes

### `workspace/TOOLS.md` — new section (insert between `### get_product_by_handle` and `## Reglas anti-alucinación`)

```markdown
### `send_product_images`

**Usa cuando**: el cliente pide explícitamente ver fotos del producto ("mándame fotos",
"tienes imagen", "¿cómo se ve?"); o el cliente eligió un producto y vas a ofrecerle
mostrarlo antes de cerrar.

**No uses cuando**: el cliente aún no ha mostrado interés concreto en el producto; el
cliente está en el cierre y ya tiene los datos; cuando aún no llamaste `search_products`
o `get_product_by_handle` para confirmar el handle.

**Contexto requerido**: lista de handles confirmados en el último `tool_result` de
`search_products` o `get_product_by_handle`. Nunca pases un handle que no hayas visto en
un `tool_result` de esta conversación.

**Side effects**: envía mensajes `type=image` por WhatsApp al cliente. Hasta 3 imágenes
por producto (default), hasta 5 productos por llamada.

**Output**: `{status, sent: [...], skipped: [...], message}`. Si `status == "partial"`,
comunica al cliente de forma natural ("para ese otro producto no tengo foto a mano,
te lo describo").

**Regla anti-spam**: máximo una invocación de `send_product_images` por turno. Si fallan
envíos, NO reintentes con la misma tool en el siguiente turno — describe el producto con
texto.
```

### `workspace/SOUL.md` — small delta (one bullet under tone guidance)

```
+ - Cuando ofrezcas mandar fotos, hazlo de forma natural ("te mando las fotos en un momento")
+   y nunca digas "cargando", "procesando" ni hagas teatro técnico visible.
```

## 7. Composition wiring

No new factory in `composition.py`. The tool receives the `CatalogPort` singleton via
constructor closure-capture in `worker.py`, identical to `SearchProductsTool`:

```python
# canonical — src/sales_whatsapp/worker.py (after existing get_product_by_handle block)
from src.sales_whatsapp.tools.images import SendProductImagesTool  # new import

register_tool_extension(
    "sales.send_product_images",
    lambda workspace: SendProductImagesTool(workspace=str(workspace), catalog=_catalog),
)
```

`_catalog` is already defined at `worker.py:49` (`_catalog = get_catalog_client()`).

## 8. Worker registration

Lines to add to `hubara_agency/src/sales_whatsapp/worker.py`:

```python
# New import (add to imports block, after GetProductByHandleTool line):
from src.sales_whatsapp.tools.images import SendProductImagesTool

# New tool extension (after the get_product_by_handle registration, ~line 58):
register_tool_extension(
    "sales.send_product_images",
    lambda workspace: SendProductImagesTool(workspace=str(workspace), catalog=_catalog),
)
```

No new `activities=[...]` entry here — that is done in F04. No `workflows=[...]` change.

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| tests/sales_whatsapp/tools/test_send_product_images_envelope.py | new | partial status, skipped, decision shape |
| tests/sales_whatsapp/tools/test_send_product_images_protocol.py | new | schema validation, ToolBase conformance |
| tests/sales_whatsapp/tools/test_send_product_images_unavailable.py | new | CatalogUnavailableError → error envelope |
| tests/sales_whatsapp/workspace/test_workspace_system_prompt.py | modify | TOOLS.md bullet present in composed prompt |

Test name list:

**test_send_product_images_envelope.py**:
- `test_partial_status_with_one_known_one_unknown` — fake CatalogPort: handle A resolves,
  handle B raises `ProductNotFoundError`. Assert `status="partial"`, `sent` has 1 entry,
  `skipped` has 1 entry with `reason="not_found"`, `send_images_decision.images` length
  equals sum of images for resolved handles capped by `max_images_per_handle`.
- `test_ok_status_all_handles_resolved` — both handles resolve. Assert `status="ok"`,
  `send_images_decision` present, all images in decision.
- `test_images_capped_at_max_images_per_handle` — product has 5 images, `max_images_per_handle=2`.
  Assert only 2 images in decision for that handle.
- `test_caption_on_first_image_only` — `caption_per_handle={"luzserena": "Vela Luz Serena"}`,
  product has 3 images. Assert first image has caption, second and third have `caption=None`.

**test_send_product_images_protocol.py**:
- `test_tool_conforms_to_toolbase_protocol` — `assert isinstance(tool, ToolBase)`.
- `test_schema_rejects_empty_handles` — call tool with `handles=[]`, assert error envelope
  or that `ToolBase` raises/returns error (depends on how `ToolBase` enforces schema).
- `test_schema_rejects_too_many_handles` — call with 6 handles, assert error envelope.

**test_send_product_images_unavailable.py**:
- `test_catalog_unavailable_returns_error_envelope` — fake CatalogPort raises
  `CatalogUnavailableError`. Assert `status="error"`, `message` contains "catálogo",
  `send_images_decision` key absent.

**test_workspace_system_prompt.py** (modify):
- Add assertion: composed system prompt for a Sales session contains the string
  `"send_product_images"` (or the exact header from TOOLS.md `### send_product_images`).

## 10. Verification commands

```bash
cd hubara_agency && uv run pytest tests/sales_whatsapp/tools/test_send_product_images_envelope.py -xvs
cd hubara_agency && uv run pytest tests/sales_whatsapp/tools/test_send_product_images_protocol.py -xvs
cd hubara_agency && uv run pytest tests/sales_whatsapp/tools/test_send_product_images_unavailable.py -xvs
cd hubara_agency && uv run pytest tests/sales_whatsapp/workspace/test_workspace_system_prompt.py -xvs
cd hubara_agency && uv run ruff check src/sales_whatsapp/tools/images.py src/sales_whatsapp/worker.py
cd hubara_agency && uv run mypy src/sales_whatsapp/tools/images.py
cd hubara_agency && uv run pytest tests/ -x -q --ignore=tests/integration
```

## 11. Definition of Done

- [ ] `hubara_agency/src/sales_whatsapp/tools/images.py` created with `SendProductImagesTool`.
- [ ] Tool resolves handles via `CatalogPort.get_by_handle`, skips `ProductNotFoundError`, handles `CatalogUnavailableError`.
- [ ] Tool returns JSON envelope with `status`, `sent`, `skipped`, `send_images_decision`, `message`.
- [ ] `send_images_decision` absent when `status="error"`.
- [ ] Caption applied only to the first image per handle.
- [ ] `register_tool_extension("sales.send_product_images", ...)` added to `worker.py`.
- [ ] `SendProductImagesTool` import added to `worker.py`.
- [ ] `workspace/TOOLS.md` new section present on disk.
- [ ] `workspace/SOUL.md` tone bullet added.
- [ ] All 8 test scenarios in §9 passing.
- [ ] All verification commands in §10 exit 0.
- [ ] No regression in existing test suite.
- [ ] R-rules check in §12 confirmed.

## 12. R-rules check

- R-DET: **not applicable** — tool is a plain async function, not a workflow.
- R-JSON: **applies** — tool builds `SendProductImagesDecision` / `ProductImagePayload`
  (from F01) and serializes them via `json.dumps` / `dataclasses.asdict`. Return type is `str`.
  No Pydantic, no generics. ✓
- R-STATELESS: **applies** — tool receives `CatalogPort` via constructor (closure-captured
  singleton, same pattern as `SearchProductsTool`). No module-level `_X = ` state. ✓
- R-HEARTBEAT: **not applicable** — tool does not run as a Temporal activity.
- R-DIP: **applies** — tool must NOT import `temporalio.client` or `src.platform.whatsapp.*`.
  The tool emits a `send_images_decision` envelope; the workflow dispatches the activity.
  Verify no WhatsApp or Temporal client imports creep in. ✓

## 13. Open questions / risks

- **`CatalogPort.get_by_handle` method name**: confirmed in refinement §5 ("Resolve handles
  via `CatalogPort.get_by_handle`"). Verify the actual method signature before implementing.
  Check `src/platform/catalog/port.py` (or `__init__.py`).
- **`thumbnail_url` field on `CatalogProductDTO`**: refinement §0 mentions `thumbnail_url`
  and `images: [{url, rank}]` as existing fields in the snapshot DTOs (`platform/catalog/dtos.py:28-44`).
  Verify exact field names before coding the image-list builder in the tool body.
- **`ToolBase` schema enforcement**: verify whether `ToolBase` in exoclaw enforces `parameters`
  schema before calling `execute_with_context`, or whether the tool must validate manually.
  If manual, add `if not handles: return json.dumps({"status": "error", "message": "..."})`.
- **`ctx.session_key` vs `ctx.session_id`**: the `ToolContext` attribute used to populate
  `SendProductImagesDecision.session_id`. Check `exoclaw.agent.tools.ToolContext` definition
  (look at how existing tools like `ManageConversationTagTool` reference the session).
