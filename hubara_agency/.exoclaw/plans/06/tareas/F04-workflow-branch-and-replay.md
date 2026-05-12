# Task F04 — Workflow send_images_decision branch, worker activity registration, and replay fixture

- Slug: workflow-branch-and-replay
- HU id: 06
- Target agent: sales_whatsapp
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §2, §10, §12)
- Planner: exoclaw-task-planner-archon
- Date: 2026-05-11
- Iteration: 1
- Estimated LOC: 125
- Risk: medium

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- AC-1: "When the LLM invokes `send_product_images(handles=["luz-serena"], caption_per_image="Vela Luz Serena")`, Then the customer receives one WhatsApp `type=image` message per image of the resolved product... each with optional caption."
- AC-4 (workflow side): "if exhausted, the workflow logs the failure and **continues the turn** — image-send failure does NOT prevent the text reply from going out."
- AC-7: "Replay fixture `tests/fixtures/history_sales_session_v3.json` exists (bumped from v2) because the workflow loop now reads a new decision field; old fixture would not replay deterministically against the new signature."

Refinement sections that informed this task: §2 (Workflow mode), §10 (Worker registration), §11 (Hard rules), §12 (Tests — workflow, replay), §14 implementation steps 9 and 12.

## 2. Dependencies

- depends_on: ['F01', 'F02', 'F03']
- blocks: []
- Inherits from upstream tasks:
  - F01: `_IMAGE_SEND_OPTIONS` (retry preset for `execute_activity`) and `TurnResult.send_images_decision` field (populated by `run_agent_turn`).
  - F02: `send_whatsapp_images_activity` (the activity this workflow branch dispatches).
  - F03: tool registered in worker (ensures `send_product_images` tool invocations produce a non-null `send_images_decision` in `TurnResult`).

## 3. Files affected

All paths are RELATIVE TO REPO ROOT. CWD for verification commands = `hubara_agency/`.

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| hubara_agency/src/sales_whatsapp/workflows/sales_session.py | modify | New `send_images_decision` branch + import | +20 |
| hubara_agency/src/sales_whatsapp/worker.py | modify (spinal) | Add `send_whatsapp_images_activity` to activities list + import | +5 |
| hubara_agency/tests/sales_whatsapp/workflows/test_sales_session_images_branch.py | new | Workflow integration test | ~70 |
| hubara_agency/tests/fixtures/history_sales_session_v3.json | new | Replay fixture capturing images branch | ~20 (JSON) |
| hubara_agency/tests/test_replay_sales.py | modify | Point at v3 fixture | +5 |

`sales_session.py` is NOT declared spinal; only F04 modifies it — no merger needed.
`worker.py` IS spinal; F03 also modifies it in B2. F04 is in B3 (runs after B2 is merged),
so no concurrent modification of `worker.py` — standard git state at B3 start already
includes F03's tool extension. F04 adds only the `send_whatsapp_images_activity` to
`activities=[...]`.

## 4. Boundary DTOs (R-JSON)

Uses `SendProductImagesDecision` and `ProductImagePayload` from F01 (indirectly via
`TurnResult.send_images_decision`). The workflow reads `result.send_images_decision.images`
to pass as the activity arg:

```python
# args=[session.session_id, result.send_images_decision.images]
# list[ProductImagePayload] crosses execute_activity — serializable (F01 confirmed).
```

## 5. Workflow snippet

### New `with workflow.unsafe.imports_passed_through():` entries in `sales_session.py`

```python
# canonical — src/sales_whatsapp/workflows/sales_session.py
# Add to the existing `with workflow.unsafe.imports_passed_through():` block:
from src.platform.temporal.retry_policies import _IMAGE_SEND_OPTIONS  # new
from src.platform.whatsapp.activities import send_whatsapp_images_activity  # new
```

### New branch inside the per-turn dispatch block (~after line 114)

```python
# canonical — insert after `if result.transfer_decision is not None:` block (~line 114)
if result.send_images_decision is not None:
    try:
        await workflow.execute_activity(
            send_whatsapp_images_activity,
            args=[
                session.session_id,
                result.send_images_decision.images,
            ],
            **_IMAGE_SEND_OPTIONS,  # type: ignore[arg-type]
        )
    except Exception:
        workflow.logger.warning(
            "send_whatsapp_images_activity failed for session {}; continuing turn",
            session.session_id,
        )
# The text reply (send_whatsapp_message_activity) follows unconditionally — AC-4 fulfilled.
```

**Placement rule** (from refinement §2): images are dispatched **before** the text reply.
Insert after the `transfer_decision` branch and before the `send_whatsapp_message_activity`
call at lines 116-123.

**R-DET compliance**: no `time.time()`, `random`, or `os.environ` in the workflow code.
The `try/except Exception` is a deterministic catch; the log call uses `workflow.logger`
(Temporal-deterministic). ✓

### Worker registration change

```python
# canonical — src/sales_whatsapp/worker.py
# New import (add after existing send_whatsapp_message_activity import, ~line 12):
from src.platform.whatsapp.activities import (
    send_whatsapp_message_activity,
    send_whatsapp_images_activity,  # new
)

# In activities=[...] list inside Worker(...) constructor (~line 76), append:
    send_whatsapp_images_activity,
```

## 6. Workspace changes

None — workspace files already handled by F03.

## 7. Composition wiring

No factory changes. `send_whatsapp_images_activity` is a function, not a factory.

## 8. Worker registration

Exact lines added to `hubara_agency/src/sales_whatsapp/worker.py`:

```python
# 1. Extend the import at ~line 12:
from src.platform.whatsapp.activities import (
    send_whatsapp_message_activity,
    send_whatsapp_images_activity,  # add this
)

# 2. Append to activities=[...] in Worker(...), ~line 76:
    send_whatsapp_images_activity,
```

No `workflows=[...]` change — `HubaraSalesSessionWorkflow` is unchanged as a class.

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| tests/sales_whatsapp/workflows/test_sales_session_images_branch.py | new | Activity dispatched; failure caught; text reply still sent |
| tests/fixtures/history_sales_session_v3.json | new | Replay fixture for images branch |
| tests/test_replay_sales.py | modify | Point at v3 fixture |

Test name list:

**test_sales_session_images_branch.py** (use `WorkflowEnvironment.start_time_skipping`):
- `test_send_images_activity_called_when_decision_present` — inject a fake `execute_tool`
  that returns the images decision envelope. Assert `send_whatsapp_images_activity` was
  called exactly once with `(session_id, images_list)`. Assert `send_whatsapp_message_activity`
  was also called once for the text reply (order: images first, then text).
- `test_text_reply_sent_even_when_images_activity_fails` — `send_whatsapp_images_activity`
  mocked to raise an exception. Assert workflow does NOT raise. Assert
  `send_whatsapp_message_activity` still called for the text reply. (AC-4 workflow side.)
- `test_no_images_activity_when_decision_absent` — inject a fake `execute_tool` that returns
  a plain text response (no `send_images_decision` key). Assert `send_whatsapp_images_activity`
  never called.

**tests/fixtures/history_sales_session_v3.json** (new fixture):
- Capture a session history that includes a turn where `send_product_images` was invoked.
- Generate via `tests/fixtures/generate_fixtures.py` (referenced in refinement §12).
- The fixture must exercise the `send_images_decision is not None` branch so replay is
  deterministic against the new `TurnResult` field.

**tests/test_replay_sales.py** (modify):
- Update fixture reference from `history_sales_session_v2.json` to `history_sales_session_v3.json`.
- Confirm the v2 fixture still replays green for the no-images path (the new `TurnResult` field
  is optional; old history that never triggers the images branch should still pass).

## 10. Verification commands

```bash
cd hubara_agency && uv run pytest tests/sales_whatsapp/workflows/test_sales_session_images_branch.py -xvs
cd hubara_agency && uv run pytest tests/test_replay_sales.py -xvs
cd hubara_agency && uv run ruff check src/sales_whatsapp/workflows/sales_session.py src/sales_whatsapp/worker.py
cd hubara_agency && uv run mypy src/sales_whatsapp/workflows/sales_session.py src/sales_whatsapp/worker.py
cd hubara_agency && uv run pytest tests/ -x -q --ignore=tests/integration
```

## 11. Definition of Done

- [ ] `send_images_decision is not None` branch added to `sales_session.py` BEFORE the `send_whatsapp_message_activity` call.
- [ ] Branch wrapped in `try/except Exception` with `workflow.logger.warning` on failure.
- [ ] `send_whatsapp_images_activity` imported in `sales_session.py` inside `workflow.unsafe.imports_passed_through()`.
- [ ] `_IMAGE_SEND_OPTIONS` imported in `sales_session.py` inside `workflow.unsafe.imports_passed_through()`.
- [ ] `send_whatsapp_images_activity` added to `activities=[...]` in `worker.py`.
- [ ] Import for `send_whatsapp_images_activity` added to `worker.py`.
- [ ] All 3 workflow test scenarios in §9 passing.
- [ ] `tests/fixtures/history_sales_session_v3.json` present and captures the images branch.
- [ ] `tests/test_replay_sales.py` updated to reference v3 fixture and green.
- [ ] All verification commands in §10 exit 0.
- [ ] No regression in existing test suite.
- [ ] R-rules check in §12 confirmed.

## 12. R-rules check

- R-DET: **applies** — the new branch uses `workflow.execute_activity` (deterministic),
  `workflow.logger` (deterministic), and a pure `if result.send_images_decision is not None`
  conditional. Zero `time.time()`, `random`, `os.environ`. `try/except` is deterministic.
  The `_IMAGE_SEND_OPTIONS` dict contains `timedelta` values (serializable, Temporal-safe). ✓
- R-JSON: **applies** — `result.send_images_decision.images` (`list[ProductImagePayload]`) is
  passed as the second positional arg to `execute_activity`. Framework serializes it via
  dataclass-to-dict. No complex generics. ✓
- R-STATELESS: **not applicable** — workflow itself holds no mutable class-level state beyond
  `_pending`, `_last_response`, `_processing`, `_force_shutdown` (already present). New branch
  adds no module-level state. ✓
- R-HEARTBEAT: **not applicable** — the workflow dispatches the activity; heartbeat is on the
  activity side (F02).
- R-DIP: **applies** — workflow imports `send_whatsapp_images_activity` via
  `workflow.unsafe.imports_passed_through()`. Does not import `httpx`, `whatsapp_client`, or
  any infrastructure client directly. ✓

## 13. Open questions / risks

- **Replay fixture generation**: `tests/fixtures/generate_fixtures.py` must be run with the
  new code path exercised (a session that invokes `send_product_images` at least once).
  This requires either (a) a local worker run with real credentials, or (b) manually crafting
  the v3 fixture JSON by modifying v2 to include the new `send_images_decision` event.
  Recommended: use option (b) for CI; note in PR that a real-session regeneration is pending.
- **v2 fixture backward compatibility**: The new `TurnResult.send_images_decision` field has
  `default=None` (non-frozen dataclass). Old history that never triggers the images branch
  should still replay green. Verify this assumption with a quick `uv run pytest tests/test_replay_sales.py`
  pointing at the v2 fixture first, before replacing.
- **`_IMAGE_SEND_OPTIONS` type: ignore comment**: the existing dispatch pattern in
  `sales_session.py` uses `**_LLM_OPTIONS, # type: ignore[arg-type]`. Apply the same comment
  to `**_IMAGE_SEND_OPTIONS` to suppress mypy `TypedDict` mismatch.
- **`try/except Exception` scope**: should the catch include ALL exceptions (broad) or only
  `temporalio.exceptions.ActivityError`? The refinement says "if exhausted, the workflow
  logs the failure and continues." Recommended default: catch `Exception` broadly (same
  pattern as other degraded-UX situations in the codebase) and log with `workflow.logger.warning`.
  Narrow to `ActivityError` if team prefers strict exception types.
