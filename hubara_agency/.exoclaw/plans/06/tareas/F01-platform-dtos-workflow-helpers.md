# Task F01 — Platform DTOs, TurnResult extension, and retry preset

- Slug: platform-dtos-workflow-helpers
- HU id: 06
- Target agent: sales_whatsapp
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §3, §4, §11)
- Planner: exoclaw-task-planner-archon
- Date: 2026-05-11
- Iteration: 1
- Estimated LOC: 60
- Risk: low

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- AC-1 (partial — DTOs enable the decision envelope the full flow depends on): "When the LLM invokes `send_product_images(handles=["luz-serena"], ...)`, Then the customer receives one WhatsApp `type=image` message per image..."
- AC-2 (partial — DTO shape enables partial/skipped response): "Given the LLM invokes `send_product_images(handles=["non-existent"])`, Then the tool returns `{status: partial, sent: [], skipped: [...]}`..."
- AC-4 (partial — retry options constant used by workflow to call the activity): "Given a Meta-side transient failure (HTTP 5xx), Then the activity retries per `_IMAGE_SEND_OPTIONS`..."

Refinement sections that informed this task: §3 (Boundary DTOs), §4 (Activities — retry preset), §11 (Hard rules check).

## 2. Dependencies

- depends_on: []
- blocks: ['F02', 'F03']
- Inherits from upstream tasks: none (foundation)

## 3. Files affected

All paths are RELATIVE TO REPO ROOT. CWD for verification commands = `hubara_agency/`.

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| hubara_agency/src/platform/contracts.py | modify | Add `ProductImagePayload` + `SendProductImagesDecision` DTOs | +22 |
| hubara_agency/src/platform/workflow_helpers.py | modify | Extend `TurnResult` + `run_agent_turn` parse block | +18 |
| hubara_agency/src/platform/temporal/retry_policies.py | modify | Add `_IMAGE_SEND_OPTIONS` constant | +10 |
| hubara_agency/tests/test_imports.py | modify | Smoke assertions for new symbols | +10 |

`workflow_helpers.py` and `retry_policies.py` are NOT declared in `.exoclaw/spinal-files.yaml`.
Only F01 modifies them; no parallel batch conflict arises.

## 4. Boundary DTOs (R-JSON)

```python
# canonical — src/platform/contracts.py (append after ScheduleRemarketingDecision)
@dataclass
class ProductImagePayload:
    """Una imagen individual a enviar via WhatsApp type=image (from refinement §3)."""
    handle: str
    image_url: str
    caption: str | None = None
    rank: int = 0


@dataclass
class SendProductImagesDecision:
    """Decision emitida por SendProductImagesTool (ADR-001, from refinement §3)."""
    session_id: str
    images: list[ProductImagePayload]
```

**NOTE on frozen=True**: refinement §3 says `frozen=True` but the existing `TransferDecision`
and `ScheduleRemarketingDecision` are plain (non-frozen) dataclasses. Keep consistent with
the existing convention — omit `frozen=True`. DTOs that cross `execute_activity` work either
way; consistency trumps the refinement's suggestion here. Flag in PR if team wants frozen.

Reused from `exoclaw_temporal.config`: none for these DTOs. They live in `platform/contracts.py`
to match the existing `TransferDecision`/`ScheduleRemarketingDecision` pattern.

## 5. Workflow helpers snippet (TurnResult + run_agent_turn)

```python
# canonical — src/platform/workflow_helpers.py
# 1. New import (inside `with workflow.unsafe.imports_passed_through():` block, ~line 33):
from src.platform.contracts import (
    ScheduleRemarketingDecision, TransferDecision,
    SendProductImagesDecision, ProductImagePayload,  # add these two
)

# 2. TurnResult — add one field (after `schedule_remarketing`, ~line 79):
@dataclass
class TurnResult:
    final_content: str
    tools_used: list[str] = field(default_factory=list)
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None
    send_images_decision: SendProductImagesDecision | None = None  # NEW

# 3. In run_agent_turn body — declare variable alongside peers (~line 142):
send_images_decision: SendProductImagesDecision | None = None

# 4. In the payload parse block (~after line 190), add:
if "send_images_decision" in payload and isinstance(
    payload["send_images_decision"], dict
):
    sid = payload["send_images_decision"]
    send_images_decision = SendProductImagesDecision(
        session_id=str(sid.get("session_id", session.session_id)),
        images=[ProductImagePayload(**img) for img in sid.get("images", [])],
    )

# 5. In TurnResult(...) constructor (~line 220-224), add kwarg:
    send_images_decision=send_images_decision,
```

## 6. Workspace changes

None — this task is entirely platform-layer. No workspace files touched.

## 7. Retry preset snippet

```python
# canonical — src/platform/temporal/retry_policies.py (append after _CONV_OPTIONS)
_IMAGE_SEND_OPTIONS = {
    # multi-image bursts ≤5 products × 3 imgs × ~30s p99 + 1.5s sleep between
    "start_to_close_timeout": timedelta(minutes=3),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        maximum_attempts=3,
        initial_interval=timedelta(seconds=2),
    ),
}
```

`_IMAGE_SEND_OPTIONS` is NOT the activity's own timeout — it is the **caller-side** dict passed
as `**_IMAGE_SEND_OPTIONS` by the workflow (F04) to `workflow.execute_activity(...)`. The
activity enforces the timeout on Temporal's side.

## 8. Worker registration

No worker changes in this task. DTOs are platform-level; no tool/activity registration
belongs here.

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| tests/test_imports.py | modify | Smoke-import 3 new symbols to keep boundary imports honest |

Test names to add to `tests/test_imports.py`:
- `test_import_send_product_images_decision` — `from src.platform.contracts import SendProductImagesDecision`
- `test_import_product_image_payload` — `from src.platform.contracts import ProductImagePayload`
- `test_import_image_send_options` — `from src.platform.temporal.retry_policies import _IMAGE_SEND_OPTIONS`

If `tests/test_imports.py` does not exist, create it as a minimal module with these three
one-liner import tests (see §13 open question).

## 10. Verification commands

```bash
cd hubara_agency && uv run pytest tests/test_imports.py -xvs
cd hubara_agency && uv run ruff check src/platform/contracts.py src/platform/workflow_helpers.py src/platform/temporal/retry_policies.py
cd hubara_agency && uv run mypy src/platform/contracts.py src/platform/workflow_helpers.py src/platform/temporal/retry_policies.py
cd hubara_agency && uv run pytest tests/ -x -q --ignore=tests/integration
```

## 11. Definition of Done

- [ ] `ProductImagePayload` and `SendProductImagesDecision` added to `hubara_agency/src/platform/contracts.py`.
- [ ] `TurnResult.send_images_decision` field added to `hubara_agency/src/platform/workflow_helpers.py`.
- [ ] `send_images_decision` variable declared and populated in `run_agent_turn` parse block.
- [ ] `SendProductImagesDecision` returned in `TurnResult(...)` constructor.
- [ ] `_IMAGE_SEND_OPTIONS` added to `hubara_agency/src/platform/temporal/retry_policies.py`.
- [ ] Smoke import tests in `tests/test_imports.py` pass for all 3 new symbols.
- [ ] All verification commands in §10 exit 0.
- [ ] No regression in existing test suite (`uv run pytest tests/ -x -q`).
- [ ] R-rules check in §12 confirmed.

## 12. R-rules check

- R-DET: **not applicable** — no workflow code introduced here; `workflow_helpers.py` is
  already deterministic (only `workflow.execute_activity` calls and pure logic). Extending
  `TurnResult` and adding a parse block follows the same pattern.
- R-JSON: **applies** — `ProductImagePayload` and `SendProductImagesDecision` are plain
  `@dataclass` with `str / int / list[...] / str | None` fields. No Pydantic, no generics.
  They cross `execute_activity` serialization cleanly.
- R-STATELESS: **not applicable** — no activities in this task.
- R-HEARTBEAT: **not applicable** — no activities in this task.
- R-DIP: **not applicable** — `contracts.py` imports only `dataclasses`; `workflow_helpers.py`
  imports already gated behind `workflow.unsafe.imports_passed_through()`. New import of
  `SendProductImagesDecision` goes in the same gated block.

## 13. Open questions / risks

- **`tests/test_imports.py` existence**: the refinement cites it as an existing file. Verify it
  exists at `hubara_agency/tests/test_imports.py` before modifying. If missing, create it as
  a new module with the three import tests (no other scaffolding needed).
- **`ProductImagePayload` in `run_agent_turn` parse block**: the `**img` spread assumes all
  fields of `ProductImagePayload` are present in the JSON. If the tool emits extra fields (e.g.
  debugging metadata), the spread will raise `TypeError`. Recommended default: use explicit
  field extraction (`handle=img["handle"], image_url=img["image_url"], ...`) rather than `**img`.
  Flag for implementer decision.
- **frozen=True discrepancy**: refinement §3 says `frozen=True` for `SendProductImagesDecision`
  but existing peers are not frozen. Use non-frozen for consistency; note in PR.
