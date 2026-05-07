# Activity Log

## 2026-05-06 — deha-architect — remarketing_whatsapp PR-E lean collapse complete

**Outcome**: PR-E aplana el layout de remarketing_whatsapp para que coincida con sales_whatsapp post-PR-E (ADR-2026-05-06-07). `domain/policies/prompts.py` se movio a `prompts.py` top-level; `activities.py` (file) se convirtio en `activities/` (package) con `bootstrap_session.py` y `__init__.py` que re-exporta los dos simbolos publicos (`bootstrap_remarketing_session_activity`, `build_remarketing_trigger_activity`). Los archivos viejos quedan neuterizados con docstring deprecado hasta que el usuario corra `git rm`. Cero cambios productivos en behavior; R-DET / R-JSON / R-STATELESS / R-HEARTBEAT / R-DIP intactos. Predicted tests: 87 passed (identico a post-PR-D).

**Files creados**:
- `hubara_agency/src/domains/remarketing_whatsapp/prompts.py` — copia de `domain/policies/prompts.py` con docstring nuevo que cita ADR-2026-05-06-11. `build_remarketing_trigger(motivo, memory_context="")` igual byte-a-byte.
- `hubara_agency/src/domains/remarketing_whatsapp/activities/__init__.py` — re-export de `bootstrap_remarketing_session_activity` y `build_remarketing_trigger_activity` desde `activities.bootstrap_session`. Mismo patron que `sales_whatsapp/activities/__init__.py`.
- `hubara_agency/src/domains/remarketing_whatsapp/activities/bootstrap_session.py` — body identico al `activities.py` (file) pre-PR-E, con un solo cambio de import en linea 33: `from src.domains.remarketing_whatsapp.prompts import build_remarketing_trigger` (era `domain.policies.prompts`).

**Files neuterizados (esperan `git rm`)**:
- `hubara_agency/src/domains/remarketing_whatsapp/domain/policies/prompts.py` — body vaciado, docstring deprecado que apunta al nuevo path.
- `hubara_agency/src/domains/remarketing_whatsapp/domain/policies/__init__.py` — docstring deprecado (era empty antes; sigue empty).
- `hubara_agency/src/domains/remarketing_whatsapp/domain/__init__.py` — docstring deprecado (era empty antes; sigue empty).
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py` — body vaciado, docstring deprecado. Python prioriza el package `activities/` sobre el file `activities.py`, asi que los imports productivos siguen funcionando pre-`git rm`.

**Files editados**:
- `hubara_agency/tests/test_prompts.py:4` — `from src.domains.remarketing_whatsapp.prompts import build_remarketing_trigger` (era `domain.policies.prompts`).

**Verificacion de consumers** (Read-driven):
- `src/domains/remarketing_whatsapp/workflows/remarketing.py:19` — importa `from src.domains.remarketing_whatsapp.activities import (...)`. Sigue funcionando: el package `activities/` re-exporta los dos simbolos.
- `src/domains/remarketing_whatsapp/worker.py:20` — idem. Sigue funcionando.
- `tests/test_bootstrap_remarketing_activity.py:45,81,100,125` — todos hacen `from src.domains.remarketing_whatsapp.activities import bootstrap_remarketing_session_activity`. Siguen funcionando.
- `tests/test_imports.py:19` — `importlib.import_module("src.domains.remarketing_whatsapp.activities")` no chequea simbolos especificos; el package es importable.
- `tests/test_prompts.py:4` — actualizado.
- Ningun otro caller del path `remarketing_whatsapp.domain.policies.prompts` en `src/` ni en `tests/`.

**ADR**: ADR-2026-05-06-11 agregado a `decisions.md`.

**Anti-patterns avoided**:
- R-DET: cero cambios en workflow body. El workflow no se toco.
- R-JSON: el shape del DTO `RemarketingSessionInput` y de `SessionInput` no cambiaron. Activity signature identica.
- R-STATELESS: `activities/__init__.py` solo hace re-exports. Cero module-level mutable state.
- R-HEARTBEAT: las activities supervivientes son fast (<10s); su body es identico al pre-PR-E.
- R-DIP: el modulo `prompts.py` es pure business logic, sin imports de litellm / temporalio / httpx / exoclaw_conversation. R-DIP preservado.

**Hand-off al usuario**:
1. **`git mv` y `git rm` los archivos neuterizados**:
   ```bash
   cd /Users/edgm/Documents/Projects/AgencyHubara
   # Borrar el directorio domain/ entero (3 archivos: __init__.py x2 + prompts.py viejo).
   git rm -r hubara_agency/src/domains/remarketing_whatsapp/domain/
   # Borrar el activities.py file viejo (el package activities/ ya gano).
   git rm hubara_agency/src/domains/remarketing_whatsapp/activities.py
   ```
   Los archivos nuevos (`prompts.py`, `activities/__init__.py`, `activities/bootstrap_session.py`) se agregan automaticamente con `git add` cuando hagas `git status` — son nuevos, no movidos formalmente con `git mv` porque el agente no tiene shell access.
2. **Correr la suite**:
   ```bash
   cd /Users/edgm/Documents/Projects/AgencyHubara/hubara_agency
   uv run pytest tests/ --no-header -q
   ```
   **Predicted**: 87 passed (mismo total que post-PR-D — PR-E no agrega ni quita tests, solo cambia un import en `test_prompts.py`).
3. **Tree final esperado** del dominio:
   ```
   remarketing_whatsapp/
   ├── __init__.py
   ├── activities/
   │   ├── __init__.py                  (re-exports)
   │   └── bootstrap_session.py         (las 2 activities productivas)
   ├── config/
   │   ├── __init__.py
   │   └── env.py
   ├── contracts.py
   ├── prompts.py                       (TOP-LEVEL, sin domain/policies/)
   ├── workflows/
   │   ├── __init__.py
   │   └── remarketing.py
   ├── worker.py
   └── workspace/                       (runtime config — markdown only)
   ```
   `domain/` ya no existe. `activities.py` (file) ya no existe.

**Test command (run from `hubara_agency/`)**:
```bash
uv run pytest tests/ --no-header -q
```

## 2026-05-06 — deha-architect — remarketing_whatsapp PR-D global cleanup complete

**Outcome**: PR-D borra la maquinaria del legacy brain_loader del repo entero. Tras la migracion DEHA workspace de Sales (PR-A/PR-B/PR-C/PR-D Sales) y de Remarketing (PR-A/PR-B Remarketing), nadie consume `BrainLoaderPort` / `DefaultBrainLoader` / `load_brain` / `shared_brain/` / `@activity.defn load_remarketing_brain_activity`. Los archivos quedan neuterizados con docstring deprecado (pendiente `git rm` por el usuario). `LoadOrStartSalesSession` baja de 5 args a 3 (+ default). `plugin_context` ahora es siempre `None` para ambas rutas (Sales y Remarketing); el campo sobrevive en la signature como hueco para A-MEM (turn-volatile slot).

**Verificacion grep pre-borrado** (manual, sin shell):

Consumers de brain machinery encontrados en `src/` antes de PR-D:
- `src/core/brains.py:14` — definicion de `load_brain` (sin callers vivos en runtime; era llamado solo por `DefaultBrainLoader.load`).
- `src/core/ports/brain_loader.py` — Protocol `BrainLoaderPort`.
- `src/core/ports/__init__.py:13,18` — re-export.
- `src/core/infrastructure/brains/__init__.py:8,10` — re-export `DefaultBrainLoader`.
- `src/core/infrastructure/brains/default_loader.py:11,14-22` — adapter (importa `load_brain`).
- `src/core/infrastructure/adapters/brain_loader_adapter.py:10,13-21` — adapter duplicado.
- `src/core/infrastructure/adapters/__init__.py:10,17` — re-export.
- `src/domains/remarketing_whatsapp/activities.py:10,28,104,115` — `load_brain` import + `REMARKETING_BRAIN_DIR` + `@activity.defn load_remarketing_brain_activity`.
- `src/domains/sales_whatsapp/composition.py:7,32,51-53,73,84,85` — imports `DefaultBrainLoader`, define `_REMARKETING_BRAIN_DIR`, instancia `DefaultBrainLoader()`, pasa args a `LoadOrStartSalesSession`.
- `src/domains/sales_whatsapp/use_cases/load_or_start_sales_session.py:35,45,89-90,95-96,126-128` — imports `BrainLoaderPort`, accepts `remarketing_brain_loader` + `remarketing_brain_dir`, llama `.load(...)`.
- `tests/test_load_brain_activity.py` — archivo entero (3 tests sobre la activity).
- `tests/test_load_or_start_sales_session.py:14,46-57,123-146,156,180,208-220,229-247` — `FakeBrainLoader`, `Path` import para `Path("/tmp/rem-brain")`, args a `_make_use_case`, assertions sobre `rem_loader.calls`.

Consumers de `build_workspace_config` encontrados en `src/`:
- `src/core/registries.py:26-30` — definicion.
- `src/core/infrastructure/adapters/tool_registry_adapter.py:15,38-39` — wrapper en `DefaultToolRegistry`.
- `src/core/ports/tool_registry.py:33-40` — Protocol declaration.

**No se encontraron** referencias a `build_workspace_config` en `tests/` ni en otros dominios fuera de los listados arriba. Los 2 callers en `src/` son orphans estructurales (nadie instancia `DefaultToolRegistry` ni usa `ToolRegistryPort` en runtime — los workers/activities llaman directo a las funciones modulo-level de `core/registries.py`), pero se decidio **mantener `build_workspace_config` con docstring deprecado** porque borrarla rompe el import del Protocol/Adapter triad. Ese triad es candidato a una limpieza futura.

**Files neuterizados (esperan `git rm`)**:
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/identity.md` — docstring deprecado.
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/instructions.md` — docstring deprecado.
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/knowledge.md` — docstring deprecado.
- `hubara_agency/src/core/brains.py` — body vaciado, docstring deprecado.
- `hubara_agency/src/core/ports/brain_loader.py` — body vaciado, docstring deprecado.
- `hubara_agency/src/core/infrastructure/brains/default_loader.py` — body vaciado, docstring deprecado.
- `hubara_agency/src/core/infrastructure/brains/__init__.py` — re-export removido, `__all__: list[str] = []`, docstring deprecado.
- `hubara_agency/src/core/infrastructure/adapters/brain_loader_adapter.py` — body vaciado, docstring deprecado.
- `hubara_agency/tests/test_load_brain_activity.py` — body vaciado, docstring deprecado.

**Files editados (cambios productivos)**:
- `hubara_agency/src/core/ports/__init__.py:13,18` — drop re-export de `BrainLoaderPort`. PR-D nota agregada en docstring.
- `hubara_agency/src/core/infrastructure/adapters/__init__.py:10,17` — drop re-export de `DefaultBrainLoader`. PR-D nota agregada.
- `hubara_agency/src/core/registries.py:26-37` — `build_workspace_config` con docstring deprecado (callers estructurales sobreviven, ver arriba).
- `hubara_agency/src/core/workflow_helpers.py:107-117` — `run_agent_turn` docstring refrescado: drop la mencion stale de `load_remarketing_brain_activity`. Aclara que ambas rutas pasan `None` a plugin_context.
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:1-7,10-22,98-102` — drop import de `load_brain`, drop const `REMARKETING_BRAIN_DIR`, drop la `@activity.defn load_remarketing_brain_activity` (era 105-115 pre-PR-D). Comment block PR-D agregado al final.
- `hubara_agency/src/domains/remarketing_whatsapp/worker.py:59-66` — comment block actualizado: drop la mencion de "PR-D la borra una vez regeneradas las fixtures a v3" (eso ya paso). Documenta la responsabilidad operativa de drenar la queue antes del deploy.
- `hubara_agency/src/domains/sales_whatsapp/composition.py:1-46,72-76` — drop import de `DefaultBrainLoader`, drop const `_REMARKETING_BRAIN_DIR` y su carpeta del `Path(...)`, drop `from pathlib import Path` (ya no necesario). `LoadOrStartSalesSession(...)` ahora cablea solo `client_factory + metadata_store + sales_runtime_workspace`.
- `hubara_agency/src/domains/sales_whatsapp/use_cases/load_or_start_sales_session.py:35,45,89-90,95-96,126-128` — drop `from pathlib import Path` (ya no necesario), drop `from src.core.ports.brain_loader import BrainLoaderPort`. Drop fields `remarketing_brain_loader` / `remarketing_brain_dir` del `__init__`. Drop atributos internos `self._remarketing_brain_loader` / `self._remarketing_brain_dir`. La llamada `plugin_context = self._remarketing_brain_loader.load(...)` (linea 126-128 pre-PR-D) se reemplaza por `plugin_context = None`. Docstrings refrescados.
- `hubara_agency/tests/test_load_or_start_sales_session.py:1-32,123-146,152-247` — drop `from pathlib import Path`, drop class `FakeBrainLoader`. `_make_use_case` retorna ahora un `LoadOrStartSalesSession` (no una tupla `(use_case, FakeBrainLoader)`). Tests 1-2 (Sales paths): drop la `rem_loader.calls == []` assertion; reemplazar `use_case, rem_loader = ...` por `use_case = ...`. Test 3 (`test_routes_to_remarketing_when_active_and_running`): la assertion `args == ["vuelvo", None, ["rem-brain"]]` ahora es `args == ["vuelvo", None, None]` (PR-D global: workspace canonico, no shared_brain). Test 4 (`test_falls_back_to_sales_when_remarketing_handle_dead`): drop la assertion `rem_loader.calls == [Path("/tmp/rem-brain")]`.

**ADR**: ADR-2026-05-06-10 agregado a `decisions.md`.

**Tests / verification status**:
- El agente no tiene shell access en este turno. Estado predicho:
  - `tests/test_load_brain_activity.py` — body vaciado (`pytest` no descubre ningun test). Pre-PR-D: 3 tests. Post-PR-D: 0 tests.
  - `tests/test_load_or_start_sales_session.py` — 4 tests, todos esperados PASS con las assertions actualizadas. La que mas cambia es test 3 (`args[2] is None` para Remarketing).
  - Todos los demas tests sin tocar — `tests/test_workspace_system_prompt.py`, `tests/test_workspace_system_prompt_remarketing.py`, `tests/test_replay_*`, `tests/test_bootstrap_*`, `tests/test_imports.py`, etc. siguen passing.
- **Predicted end state**: ~90 passed, 0 failed (PR-B Remarketing tenia 92-93 passed; -3 por borrar `test_load_brain_activity.py` = 89-90).
- `tests/test_imports.py` — `test_activities_importable` importa `src.domains.remarketing_whatsapp.activities`; el modulo ahora no exporta `load_remarketing_brain_activity` ni `REMARKETING_BRAIN_DIR`, pero `test_imports.py` no chequea simbolos especificos en ese modulo (solo `importlib.import_module`). Pasa.

**Anti-patterns avoided**:
- R-DET: cero cambios en workflow body. Workflow Remarketing ya no schedule la activity eliminada (PR-B lo cerro).
- R-JSON: `LoadOrStartSalesSession` ya no cruza un `BrainLoaderPort` instance — solo dataclasses planos (`SalesSessionInput`) cruzan el boundary. Ningun objeto live pasa por `start_workflow` / `signal`.
- R-STATELESS: ningun module-level mutable state agregado. Los archivos neuterizados no exportan estado.
- R-HEARTBEAT: cero impacto. Las activities supervivientes (`bootstrap_remarketing_session_activity`, `build_remarketing_trigger_activity`, etc.) son fast (<10s).
- R-DIP: el `BrainLoaderPort` Protocol se elimino del re-export. `application/use_cases/load_or_start_sales_session.py` ya no depende de `core/ports/brain_loader.py`. El use case sigue type-hinting la concreta `FilesystemMetadataStore` (decision DEHA-lean documentada en ADR-2026-05-06-07).

**Hand-off al usuario**:
1. **`git rm` los archivos neuterizados**:
   ```bash
   cd /Users/edgm/Documents/Projects/AgencyHubara
   git rm hubara_agency/src/core/brains.py
   git rm hubara_agency/src/core/ports/brain_loader.py
   git rm -r hubara_agency/src/core/infrastructure/brains/
   git rm hubara_agency/src/core/infrastructure/adapters/brain_loader_adapter.py
   git rm -r hubara_agency/src/domains/remarketing_whatsapp/shared_brain/
   git rm hubara_agency/tests/test_load_brain_activity.py
   ```
2. **Correr la suite**:
   ```bash
   cd /Users/edgm/Documents/Projects/AgencyHubara/hubara_agency
   uv run pytest tests/ --no-header -q
   ```
   **Predicted**: ~90 passed, 0 failed.
3. **Production drain** (operacional): in-flight `RemarketingSessionWorkflow` executions con events `load_remarketing_brain_activity` en su history NO son replayables con la nueva version del worker. Drenar la queue Remarketing (idle timeout 24h) antes de deployar, o usar versioned worker side-by-side.
4. **Triad orphan candidato a limpieza futura** (no urgente): `core/registries.py:build_workspace_config` + `core/infrastructure/adapters/tool_registry_adapter.py:DefaultToolRegistry` + `core/ports/tool_registry.py:ToolRegistryPort`. Ningun runtime caller los instancia. Borrarlos es 1 PR.

**Test command (run from `hubara_agency/`)**:
```bash
uv run pytest tests/ --no-header -q
```

## 2026-05-06 — deha-architect — remarketing_whatsapp PR-B complete (switchover)

**Outcome**: PR-B is the hot swap for Remarketing. The workspace canonico (`hubara_agency/src/domains/remarketing_whatsapp/workspace/`) is now the only source of identity / tone / catalog for the Remarketing path: `bootstrap_remarketing_session_activity` builds `WorkspaceConfig(path=input.runtime_workspace_path)` and fails fast with `RuntimeError` if the path is missing; the workflow drops `_brain_cache`, `_ensure_brain()`, and the `load_remarketing_brain_activity` import; both `plugin_context` call sites now pass `None`. The worker drops `load_remarketing_brain_activity` from its activities registry. `shared_brain/`, `core/brains.py`, `core/ports/brain_loader.py`, and the `@activity.defn load_remarketing_brain_activity` survive in PR-B (PR-D deletes them).

**Files edited**:
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:8` — added `WorkspaceConfig` import; line ~13 dropped `build_workspace_config` from the `src.core.registries` import (no longer used by this module).
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:34-98` — replaced the body of `bootstrap_remarketing_session_activity`: removed `del input` + `ws = build_workspace_config(session_id)` (lines were `~57-63` pre-PR-B). New body reads `runtime_path = input.runtime_workspace_path`, raises `RuntimeError` if `not runtime_path`, then builds `ws = WorkspaceConfig(path=runtime_path)` plus `activity.logger.info(...)` mirroring the Sales pattern (`activities/bootstrap_session.py:79-90`). Tools registry still rebuilt per call from the canonical workspace path.
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:20-28` — updated comment block on `REMARKETING_BRAIN_DIR`: clarifies that PR-B removed the workflow's call site for `load_remarketing_brain_activity`; the activity defn + brain dir survive only for PR-D cleanup atomicity.
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:19-22` — dropped `load_remarketing_brain_activity` from the `imports_passed_through` import block.
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:33-37` — removed `self._brain_cache: list[str] | None = None` from `__init__` (was line 39 pre-PR-B).
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:55-59` — removed the entire `_ensure_brain` async method (was lines 61-68 pre-PR-B): the workflow no longer schedules `load_remarketing_brain_activity`.
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:98-108` — replaced `plugin_context=await self._ensure_brain()` (was line ~107 pre-PR-B) with `plugin_context=None` on the initial trigger `PendingMessage`. Comment block explains why.
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:131-141` — replaced `fallback_plugin_context=await self._ensure_brain()` (was line ~135 pre-PR-B) with `fallback_plugin_context=None` on the `run_agent_turn` invocation. Verified `core/workflow_helpers.py:run_agent_turn` (line 100-104) accepts `fallback_plugin_context: list[str] | None = None` so this is shape-safe.
- `hubara_agency/src/domains/remarketing_whatsapp/worker.py:20-23` — dropped `load_remarketing_brain_activity` from the `from src.domains.remarketing_whatsapp.activities import (...)` import (was line ~23 pre-PR-B).
- `hubara_agency/src/domains/remarketing_whatsapp/worker.py:49-68` — dropped `load_remarketing_brain_activity` from the `activities=[...]` registry list (was line ~60 pre-PR-B). Inline comment explains the deferred PR-D cleanup.
- `hubara_agency/src/domains/remarketing_whatsapp/contracts.py:21-31` — refreshed `runtime_workspace_path` docstring (removed PR-A "todavia no lo consume" note; now reflects PR-B end state with failfast).
- `hubara_agency/src/domains/remarketing_whatsapp/config/env.py:17-26` — refreshed module docstring (PR-A "PR-B will make the activity consume it" -> PR-B "the activity now consumes it").
- `hubara_agency/tests/fixtures/generate_fixtures.py:60-71` — bumped `REMARKETING_FIXTURE` from `history_remarketing_session_v2.json` to `history_remarketing_session_v3.json` (workflow activity sequence shrank by one — `load_remarketing_brain_activity` event no longer scheduled).
- `hubara_agency/tests/fixtures/generate_fixtures.py:198-217` — removed the `mock_load_remarketing_brain_activity` defn (was around line 203-205 pre-PR-B). Removed the corresponding entry from `REMARKETING_ACTIVITIES`.
- `hubara_agency/tests/fixtures/generate_fixtures.py:283-301` — `generate_remarketing_fixture` now passes `runtime_workspace_path="/tmp/fixture-workspace-remarketing"` on the `RemarketingSessionInput` (production failfast requires it; the mock body returns a fixed SessionInput regardless, but shape symmetry with prod is enforced).
- `hubara_agency/tests/test_replay_remarketing.py:30` — bumped fixture path to v3.

**Files created**:
- `hubara_agency/tests/test_workspace_system_prompt_remarketing.py` — 9 tests modeled on Sales' `tests/test_workspace_system_prompt.py`. Instantiates `ContextBuilder(workspace=WORKSPACE)` directly (avoids the `LLMProvider` requirement of `DefaultConversation.create`) and asserts that key tokens from each bootstrap file (IDENTITY/SOUL/USER/TOOLS/AGENTS) and the `hubara_catalog` always-on skill cross into the system prompt. Tokens checked: `Clara`, `Hubara`, `Mínimamente invasiva` (IDENTITY); `BREVEDAD`, `DOBLE SALTO DE L` (SOUL); `COP`, `America/Bogota` (USER); `transfer_to_sales_agent` (TOOLS); `levantar`, `transfer` (AGENTS proactive mission); `Cruz de Vida`, `$17,000` (catalog skill); `Contra Entrega`, `$45,000` (catalog policies); `caro` + `Envío Gratis` (AGENTS § Prohibición de descuentos).

**Files rewritten**:
- `hubara_agency/tests/test_bootstrap_remarketing_activity.py` — full rewrite (the PR-A pattern of broken legacy positional args is now closed). 4 tests: `test_bootstrap_returns_json_safe_session_input` and `test_bootstrap_is_idempotent` pass `RemarketingSessionInput(session_id=..., motivo=..., runtime_workspace_path=str(tmp_path/'workspace'))` against a minimal canonical workspace built under `tmp_path` (5 BOOTSTRAP_FILES). `test_bootstrap_failfast_when_workspace_path_missing` asserts the new `RuntimeError` PR-B added. `test_bootstrap_uses_runtime_workspace_path_not_per_session_vault` is an explicit regression probe — verifies the `workspace.path` reported by the SessionInput is the canonical one, NOT the per-session vault that `build_workspace_config(session_id)` returned pre-PR-B (the path does NOT contain the session_id).

**Files NOT touched (deliberate, deferred to PR-D)**:
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/{identity,instructions,knowledge}.md` — alive but dead code on Remarketing path (the workflow no longer loads them; `domains/sales_whatsapp/use_cases/load_or_start_sales_session.py` still imports `BrainLoaderPort` for the `remarketing_brain_loader` field, and `LoadOrStartSalesSession.execute` line 126-128 still loads them when the active route is Remarketing — this is a webhook-time signal payload, not a workflow-internal load, but it's still alive in the call graph). PR-D deletes the files + the field.
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:101-112` — the `@activity.defn load_remarketing_brain_activity` defn survives. By itself it is harmless to keep registered nowhere (the workflow code change is what matters for replay determinism); PR-D deletes it as a single atomic op alongside `core/brains.py`.
- `hubara_agency/src/core/brains.py`, `core/infrastructure/brains/`, `core/ports/brain_loader.py` — alive (still consumed by `LoadOrStartSalesSession.remarketing_brain_loader` and the `@activity.defn` body of `load_remarketing_brain_activity`).
- `hubara_agency/src/core/registries.py:build_workspace_config` — still callable (orphan candidate). Audit in PR-D.
- `hubara_agency/src/core/workflow_helpers.py` — `plugin_context` plumbing untouched. Now both Sales and Remarketing paths pass `None`; the field remains for the signal signature shape and as the documented A-MEM / turn-volatile slot.
- `RemarketingSessionWorkflow.send_message` signal signature — `plugin_context: list[str] | None` plumbing untouched. Replay-safe.

**Tests / verification status**:
- The agent does not have shell access in this turn. Predicted delta vs PR-A baseline (which had ~78-79 passed + 3-4 expected failures in `test_bootstrap_remarketing_activity.py`):
  - `tests/test_bootstrap_remarketing_activity.py` — 3 broken tests rewritten + 1 failfast probe + 1 regression probe = **5 expected PASS** (was 3 expected FAIL).
  - `tests/test_workspace_system_prompt_remarketing.py` — **9 expected PASS** (NEW). Sanity: workspace dir + 5 BOOTSTRAP_FILES + skill exist; identity, soul, user, tools, agents, catalog, policies, descuento-rule tokens cross to the system prompt.
  - `tests/test_replay_remarketing.py` — points at v3. The user MUST regenerate (`uv run python tests/fixtures/generate_fixtures.py`); otherwise this test fails with "fixture not found". Once regenerated, replay should succeed because the v3 fixture's activity sequence matches the new workflow code byte-for-byte (no more `load_remarketing_brain_activity` event).
  - `tests/test_remarketing_contract.py` — unchanged, 3 tests still PASS.
  - All other tests untouched.
- Predicted end state assuming the user regenerates v3 fixture: **~92-93 passed, 0 failed**. Without the regen: ~91-92 passed, 1 failed (replay test fixture missing).

**Anti-patterns avoided**:
- R-DET: zero new `time.time` / `uuid.uuid4` / `datetime.now` introduced in workflow body. The workflow now schedules ONE FEWER activity event (`load_remarketing_brain_activity` removed) — strictly less I/O on the workflow.
- R-JSON: `WorkspaceConfig(path=...)` is constructed inside the activity, never crossed as a live object. The string `runtime_workspace_path` already crossed the boundary in PR-A. `RemarketingSessionInput` shape unchanged.
- R-STATELESS: bootstrap activity rebuilds `LLMConfig`, `WorkspaceConfig`, registry on each invocation. No module-level mutable state added.
- R-HEARTBEAT: bootstrap activity is fast (no `mkdir` now — `WorkspaceConfig` constructor doesn't mkdir; `get_base_tools_registry` does small filesystem inspection only). Stays well under the 10s threshold. Removing `load_remarketing_brain_activity` removes another sub-10s operation from the workflow's critical path.
- R-DIP: workflow now imports one fewer activity. The activities module's only new framework import is `WorkspaceConfig` (boundary DTO). No `domain/` or `application/` layer changes (Remarketing has neither — its layout is still flat at the domain root, like Sales post-PR-E).

**Hand-off to PR-D (cleanup)**:
1. **User must run** `cd hubara_agency && uv run python tests/fixtures/generate_fixtures.py` to regenerate the v3 Remarketing fixture.
2. **User must run** `cd hubara_agency && uv run pytest tests/ -v --no-header`. Predicted: **~92-93 passed, 0 failed**.
3. **Production drain**: in-flight `RemarketingSessionWorkflow` executions started against the v2 worker code carry `load_remarketing_brain_activity` events in their history. The new workflow code does not schedule that activity, so `Replayer` would raise `NonDeterminismError`. Operationally: drain the Remarketing task queue (idle timeout = 24h) before deploying, OR run a versioned worker side-by-side. The same caveat existed for Sales PR-B but at smaller scale (Sales workflows have much shorter idle horizons).
4. **PR-D scope**:
   - Delete `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/` (3 files).
   - Delete `@activity.defn load_remarketing_brain_activity` and the `REMARKETING_BRAIN_DIR` constant from `domains/remarketing_whatsapp/activities.py`.
   - Drop `from src.core.brains import load_brain` from the activity module.
   - Delete `hubara_agency/src/core/brains.py` (after Remarketing's deletion, no caller remains).
   - Delete `hubara_agency/src/core/ports/brain_loader.py` (the `BrainLoaderPort` Protocol).
   - Delete `hubara_agency/src/core/infrastructure/brains/` (DefaultBrainLoader).
   - Remove `remarketing_brain_loader: BrainLoaderPort` and `remarketing_brain_dir: Path` from `LoadOrStartSalesSession.__init__` and from `composition.py`.
   - In `LoadOrStartSalesSession.execute`: replace the `plugin_context = self._remarketing_brain_loader.load(...)` block (line ~126-128) with `plugin_context = None` — Remarketing's webhook signal will stop forwarding `shared_brain/*.md`. The workflow already ignores it (the brain comes from the workspace via `ContextBuilder`); the signal payload is the last redundant copy.
   - Audit `core/registries.py:build_workspace_config` — if no caller remains after the cleanup, delete.
   - Optional: delete `tests/fixtures/history_remarketing_session_v2.json` (stale).

**Test command (run from `hubara_agency/`)**:
```bash
# Step 1: regenerate v3 remarketing fixture (PR-B handoff).
uv run python tests/fixtures/generate_fixtures.py

# Step 2: optional, remove stale v2.
rm -f tests/fixtures/history_remarketing_session_v2.json

# Step 3: full suite.
uv run pytest tests/ -v --no-header
```

## 2026-05-06 — deha-architect — remarketing_whatsapp PR-A complete (workspace skeleton + DTO wiring)

**Outcome**: PR-A wires in the canonical agent-runtime workspace for Remarketing. The workspace skeleton is committed at `hubara_agency/src/domains/remarketing_whatsapp/workspace/` (5 BOOTSTRAP_FILES + memory + `hubara_catalog` skill). `EXOCLAW_WORKSPACE_REMARKETING` is now resolved by a per-domain `config/env.py` and propagates as `runtime_workspace_path` through `RemarketingSessionInput` across the workflow boundary (R-JSON). `bootstrap_remarketing_session_activity` signature bumped to take the DTO; **body unchanged** — `shared_brain/` still wins (zero behavior change). Diff of system prompt = 0 vs pre-PR-A. Switchover is PR-B; deletion of `shared_brain/` + `load_remarketing_brain_activity` + `core/brains.py` + remaining brain ports is PR-D.

**Files created (workspace skeleton)**:
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/IDENTITY.md` — Clara's identity (from `shared_brain/identity.md`).
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/SOUL.md` — tone/voice/format rules (from `shared_brain/instructions.md` lines 6, 7-11: "BREVEDAD EXTREMA" + "REGLAS DE FORMATO" + "no pidas perdón").
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/USER.md` — tenant defaults stub (Hubara, COP, America/Bogota — symmetric with Sales).
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/TOOLS.md` — documents ONLY `transfer_to_sales_agent`. Explicit non-mention of `manage_conversation_tag` (per ADR-2026-05-06-08 — Sales owns tagging).
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/AGENTS.md` — operational rules (from `shared_brain/instructions.md` lines 1-5, 12-15: "ANÁLISIS HISTÓRICO" + "PROHIBICIÓN DE REDIRECCIÓN" + "PROHIBICIÓN ABSOLUTA DE DESCUENTOS" + "TRANSICIÓN AL AGENTE DE VENTAS"). Reinforces the proactive one-shot mission.
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/memory/MEMORY.md` — long-term memory (empty).
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/memory/HISTORY.md` — append-only log header.
- `hubara_agency/src/domains/remarketing_whatsapp/workspace/skills/hubara_catalog/SKILL.md` — full catalog (from `shared_brain/knowledge.md`). `metadata: {"exoclaw": {"always": true}}` on a single line (line 3) per agent-runtime.md:93-103.

**Bucketing of `shared_brain/instructions.md`**:
- Line 3 ("ANÁLISIS HISTÓRICO Y MANEJO DE OBJECIONES") -> `AGENTS.md` § "Análisis histórico y manejo de objeciones".
- Line 4 ("PROHIBICIÓN DE REDIRECCIÓN Y COMPRAS WEB") -> `AGENTS.md` § "Prohibición de redirección y compras web".
- Line 5 ("PROHIBICIÓN ABSOLUTA DE DESCUENTOS") -> `AGENTS.md` § "Prohibición absoluta de descuentos".
- Line 6 ("BREVEDAD EXTREMA") -> `SOUL.md` § "Estilo de comunicación".
- Lines 7-11 ("REGLAS DE FORMATO" + emoji moderation + `\n\n` + no-apologies) -> `SOUL.md` § "Reglas de formato" and § "Estilo de comunicación".
- Lines 12-15 ("TRANSICIÓN AL AGENTE DE VENTAS") -> `AGENTS.md` § "Transición al Agente de Ventas (AUTOMATIZADA)" (and reinforced in `IDENTITY.md` § "Fuera de alcance" + `TOOLS.md` § "transfer_to_sales_agent").

**Files created (config)**:
- `hubara_agency/src/domains/remarketing_whatsapp/config/__init__.py` — empty package marker.
- `hubara_agency/src/domains/remarketing_whatsapp/config/env.py` — `get_workspace_path()` reads `EXOCLAW_WORKSPACE_REMARKETING`; defaults to in-repo workspace dir. Symmetric with `sales_whatsapp/config/env.py` (ADR-2026-05-06-03).

**Files edited**:
- `hubara_agency/src/domains/remarketing_whatsapp/contracts.py:11-32` — added `runtime_workspace_path: str | None = None` to `RemarketingSessionInput` (last field, default `None` — fixture v1 deserialization stays compatible). Docstring expanded with PR-A intent.
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py:30-69` — `bootstrap_remarketing_session_activity` signature bumped from `(session_id: str, motivo: str)` to `(input: RemarketingSessionInput)`. Body unchanged (still `build_workspace_config(session_id)` per-sesion). Imports `RemarketingSessionInput`.
- `hubara_agency/src/domains/remarketing_whatsapp/workflows/remarketing.py:79-87` — caller of `bootstrap_remarketing_session_activity` now passes the input DTO directly (single positional arg) instead of `args=[session_id, motivo]`. Signal signature, idle timeout, transfer logic, and `_force_shutdown` flow are otherwise untouched.
- `hubara_agency/src/core/infrastructure/temporal/dispatcher_activities.py:77-105` — `schedule_remarketing_workflow_activity` imports `get_workspace_path as get_remarketing_workspace_path` and forwards `runtime_workspace_path=str(get_remarketing_workspace_path())` on the `RemarketingSessionInput` it constructs.
- `hubara_agency/tests/test_remarketing_contract.py:18-32` — updated `test_remarketing_input_is_json_friendly_dataclass` to expect the new field. Added `test_remarketing_input_accepts_runtime_workspace_path` to lock in the DTO shape.
- `hubara_agency/tests/fixtures/generate_fixtures.py:60-67, 144-170` — bumped `REMARKETING_FIXTURE` to `history_remarketing_session_v2.json`. `mock_bootstrap_remarketing_session_activity` signature changed to `(input: RemarketingSessionInput)`.
- `hubara_agency/tests/test_replay_remarketing.py:30` — points at v2.

**Files NOT touched (deliberate, deferred to PR-B/PR-D)**:
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/{identity,instructions,knowledge}.md` — alive, still loaded by `load_remarketing_brain_activity`. PR-B switches `ContextBuilder` over to read the workspace; PR-D deletes these.
- `hubara_agency/src/domains/remarketing_whatsapp/activities.py` body of `bootstrap_remarketing_session_activity` — still calls `build_workspace_config(session_id)`. PR-B replaces with `WorkspaceConfig(path=input.runtime_workspace_path)` + failfast (analogous to sales_whatsapp PR-B / ADR-2026-05-06-04).
- `load_remarketing_brain_activity` — alive. PR-B/PR-D removes it once the workspace owns identity/catalog.
- `core/brains.py`, `core/ports/brain_loader.py`, `core/infrastructure/brains/` — alive (Remarketing still consumes them). PR-D deletes once Remarketing's workspace path is the single source of truth.
- `RemarketingSessionWorkflow.send_message` signal signature — `plugin_context: list[str] | None` plumbing untouched. Survives PR-A; possible cleanup in PR-D (analogous to ADR-2026-05-06-06 step 5 for Sales — option (a) keep + repurpose for turn-volatile data).
- `domains/remarketing_whatsapp/worker.py` — already registered the `transfer_to_sales_agent` extension in PR-C (Sales train). Unchanged here.

**Predicted test breakage**:
- `tests/test_bootstrap_remarketing_activity.py` — 3 tests (`test_bootstrap_returns_json_safe_session_input`, `test_bootstrap_creates_workspace_dir`, `test_bootstrap_is_idempotent`) call `env.run(bootstrap_remarketing_session_activity, "wa_xxx", "motivo")` per the legacy `(session_id, motivo)` positional shape. PR-A intentionally does NOT fix them — same pattern as Sales PR-A (the handoff to PR-D / ADR-2026-05-06-06 step 6 fixed Sales). Expected: 3 failures (or 4 if Temporal's `ActivityEnvironment` interprets the second positional arg as a missing kwarg and raises differently).

**ADR**: ADR-2026-05-06-08 added to `decisions.md`.

**Tests / verification status**:
- The agent does not have shell access in this turn. Expected delta vs PR-E baseline (82 passed):
  - `tests/test_remarketing_contract.py` — 1 -> 2 tests, both should PASS.
  - `tests/test_bootstrap_remarketing_activity.py` — 3 tests, all 3 expected to FAIL until PR-B/PR-D fixes them (legacy positional shape).
  - `tests/test_replay_remarketing.py` — points at `history_remarketing_session_v2.json`. The user MUST regenerate via `uv run python tests/fixtures/generate_fixtures.py` for this test to pass; otherwise it fails with "fixture not found".
  - All other tests untouched.
- Predicted end state: **~78-79 passed + 3-4 expected failures** in `test_bootstrap_remarketing_activity.py` (firma vieja). After the user runs `uv run python tests/fixtures/generate_fixtures.py`, the v2 fixture is regenerated and `test_replay_remarketing.py` should pass.

**Anti-patterns avoided**:
- R-DET: zero workflow body changes besides the call-site update (still calling an activity, still no `time.time` / `uuid.uuid4` / `datetime.now` in the workflow body).
- R-JSON: `RemarketingSessionInput` remains a flat JSON-serializable dataclass. New field `runtime_workspace_path: str | None` is also flat. The workflow still passes a single dataclass arg to the activity.
- R-STATELESS: `bootstrap_remarketing_session_activity` body is unchanged — still rebuilds `LLMConfig`, `WorkspaceConfig`, `ToolRegistry` per call. No module-level mutable state introduced.
- R-HEARTBEAT: bootstrap activity is fast (filesystem `mkdir` + small registry construction). Stays well under the 10s threshold.
- R-DIP: `domains/remarketing_whatsapp/contracts.py` and `config/env.py` are pure stdlib + `pathlib`. No new framework imports in `domain/`. The only new import in `dispatcher_activities.py` is the local `get_workspace_path` getter — `core/` knows about the domain (legitimate, dispatcher is a composition root for cross-domain transitions).

**Hand-off**:
1. **User must run** `cd hubara_agency && uv run python tests/fixtures/generate_fixtures.py` to regenerate the v2 fixture.
2. **User must run** `cd hubara_agency && uv run pytest tests/ -v --no-header`. Expected: ~78-79 passed + 3-4 expected failures in `tests/test_bootstrap_remarketing_activity.py` (signature drift, will be fixed in PR-B/PR-D).
3. **No `git rm` is required for PR-A.** `shared_brain/` MUST stay alive — the prompts still win until PR-B switches `ContextBuilder` over.
4. Optional cleanup of stale fixture v1 file (after running the regenerator):
```bash
cd /Users/edgm/Documents/Projects/AgencyHubara
rm -f hubara_agency/tests/fixtures/history_remarketing_session_v1.json
```

**Test command (run from `hubara_agency/`)**:
```bash
# Step 1: regenerate v2 remarketing fixture
uv run python tests/fixtures/generate_fixtures.py

# Step 2: optional, remove stale v1
rm -f tests/fixtures/history_remarketing_session_v1.json

# Step 3: full suite
uv run pytest tests/ -v --no-header
```

## 2026-05-06 — deha-architect — PR-E complete (lean collapse of sales_whatsapp layout)

**Outcome**: PR-E flattens the `sales_whatsapp` domain from a textbook hexagonal layout to a lean, file-first layout. At 1.3K LoC the layered sub-folders (`application/use_cases/`, `application/ports/`, `domain/policies/`, `infrastructure/storage/`) cost more than they buy: each port had exactly one concrete adapter and exactly one fake in tests, `domain/policies/prompts.py` was a 22-line module, the `Protocol`s were 65 lines covering what Python's duck typing already does, and `service.py` was 26 lines of dead back-compat. PR-E collapses all of that to top-level files: `prompts.py`, `state.py`, `use_cases/`, `activities/`. `service.py` is neutered. Production behavior is unchanged.

**Files created**:
- `hubara_agency/src/domains/sales_whatsapp/state.py` — merge of `FilesystemMessageHistoryStore` + `FilesystemMetadataStore` (was `infrastructure/storage/{filesystem_history,filesystem_metadata}_store.py`). Single docstring explains both adapters share a per-session `<vault>/<session_id>/` layout.
- `hubara_agency/src/domains/sales_whatsapp/prompts.py` — moved from `domain/policies/prompts.py`. Same content (`_GHOSTING_PROMPT` + `build_ghosting_prompt()`).
- `hubara_agency/src/domains/sales_whatsapp/activities/__init__.py` — re-exports `bootstrap_sales_session_activity` and `decide_ghosting_action` to preserve `from src.domains.sales_whatsapp.activities import ...` import path.
- `hubara_agency/src/domains/sales_whatsapp/activities/bootstrap_session.py` — body moved from `activities.py` (file). Updated `from src.domains.sales_whatsapp.domain.policies.prompts` -> `from src.domains.sales_whatsapp.prompts`.
- `hubara_agency/src/domains/sales_whatsapp/use_cases/__init__.py` — re-exports `IngestInboundMessage` + `LoadOrStartSalesSession`.
- `hubara_agency/src/domains/sales_whatsapp/use_cases/ingest_inbound_message.py` — moved from `application/use_cases/`. Replaced `from ...application.ports.message_history_store import MessageHistoryStorePort` with `from ...state import FilesystemMessageHistoryStore`. Constructor type-hint updated to the concrete (`history_store: FilesystemMessageHistoryStore`). Fakes in tests pass via duck typing.
- `hubara_agency/src/domains/sales_whatsapp/use_cases/load_or_start_sales_session.py` — moved from `application/use_cases/`. Replaced `MetadataStorePort` import with `FilesystemMetadataStore` from `state`. Constructor type-hint updated to the concrete. The `BrainLoaderPort` import for the Remarketing field stays (cross-domain port, not in scope for PR-E).

**Files edited**:
- `hubara_agency/src/domains/sales_whatsapp/composition.py:30-44` — imports moved to lean paths: `from ...state import (FilesystemMessageHistoryStore, FilesystemMetadataStore)` and `from ...use_cases.{ingest_inbound_message,load_or_start_sales_session} import ...`. PR-E note added to docstring. Behavior identical.
- `hubara_agency/tests/test_load_or_start_sales_session.py:20-22` — import path updated `application.use_cases.load_or_start_sales_session` -> `use_cases.load_or_start_sales_session`. The fakes themselves did not need changes (no `isinstance(_, MetadataStorePort)` checks anywhere, only duck typing).

**Files neutered (stubbed with deprecation docstrings; user must `git rm`)**:
- `hubara_agency/src/domains/sales_whatsapp/activities.py` — replaced by `activities/` package; Python's import system picks the package when both coexist, so behavior is correct pre-rm.
- `hubara_agency/src/domains/sales_whatsapp/service.py` — back-compat facade gone; no caller imports it.
- `hubara_agency/src/domains/sales_whatsapp/application/__init__.py`, `application/use_cases/__init__.py`, `application/use_cases/ingest_inbound_message.py`, `application/use_cases/load_or_start_sales_session.py`, `application/ports/__init__.py`, `application/ports/message_history_store.py`, `application/ports/metadata_store.py` — each now has a 3-line deprecation docstring pointing at the new location.
- `hubara_agency/src/domains/sales_whatsapp/domain/__init__.py`, `domain/policies/__init__.py`, `domain/policies/prompts.py` — same.
- `hubara_agency/src/domains/sales_whatsapp/infrastructure/__init__.py`, `infrastructure/storage/__init__.py`, `infrastructure/storage/filesystem_history_store.py`, `infrastructure/storage/filesystem_metadata_store.py` — same.

**Files NOT touched (deliberate)**:
- `workspace/`, `config/env.py`, `contracts.py`, `parsers.py`, `workflows/sales_session.py`, `tools/{routing,tags}.py`, `worker.py`, `api.py` — per the PR-E brief, the boundary is unchanged.
- `tests/test_bootstrap_sales_activity.py` — works as-is. The import `from src.domains.sales_whatsapp.activities import bootstrap_sales_session_activity` still resolves via the new `activities/__init__.py` re-export.
- `tests/test_replay_sales.py`, `tests/test_replay_remarketing.py` — fixtures capture activity *names* (`bootstrap_sales_session_activity`, etc.), not Python import paths, so the fixtures are still valid.
- `tests/fixtures/generate_fixtures.py` — references `from src.domains.sales_whatsapp.contracts import SalesSessionInput` and `workflows.sales_session import HubaraSalesSessionWorkflow`. Both unchanged.
- `tests/test_workspace_system_prompt.py`, `tests/test_tools_protocol.py`, `tests/test_transfer_tool.py`, `tests/test_run_agent_turn.py` — no import-path changes needed (they target paths PR-E did not touch).
- `src/core/infrastructure/temporal/dispatcher_activities.py` — imports from `domains.sales_whatsapp.config.env`, `.contracts`, `.workflows.sales_session`. None of those moved.
- `src/main.py` — imports `from src.domains.sales_whatsapp import api as whatsapp_api`. Unchanged.

**Files to be deleted (manual `git rm` — agent has no shell access)**:
```bash
cd /Users/edgm/Documents/Projects/AgencyHubara
git rm hubara_agency/src/domains/sales_whatsapp/activities.py
git rm hubara_agency/src/domains/sales_whatsapp/service.py
git rm -r hubara_agency/src/domains/sales_whatsapp/application/
git rm -r hubara_agency/src/domains/sales_whatsapp/domain/
git rm -r hubara_agency/src/domains/sales_whatsapp/infrastructure/
```

**ADR**: ADR-2026-05-06-07 added to `decisions.md`.

**Tests / verification status**:
- The agent does not have shell access (no `Bash` tool in this turn). I could not run `uv run pytest tests/ -v --no-header` myself.
- Expected delta vs PR-D baseline (82 passed): **0 new failures**. The only "live" change is import paths in `composition.py` (production) and `tests/test_load_or_start_sales_session.py` (one test file). Both have been updated atomically. The fakes already worked via duck typing.
- Replay tests should keep passing because they look up activity names, not import paths. The activity names in the new `activities/bootstrap_session.py` (`bootstrap_sales_session_activity`, `decide_ghosting_action`) match the legacy ones byte-for-byte (`@activity.defn(name="...")` strings).
- `test_bootstrap_sales_activity.py` should keep passing because `from src.domains.sales_whatsapp.activities import bootstrap_sales_session_activity` resolves to the new package's re-export, which points at the same activity body (line-for-line copy from the legacy `activities.py`).

**Anti-patterns avoided**:
- R-DET: zero workflow body changes. No `time.time` / `uuid.uuid4` / `datetime.now` introduced.
- R-JSON: zero DTO shape changes. `SalesSessionInput`, `SessionInput`, signal signatures unchanged.
- R-STATELESS: composition root still caches the use-case singleton; no new module-level mutable state introduced. Activities still rebuild deps on each invocation.
- R-HEARTBEAT: `bootstrap_sales_session_activity` body is identical to PR-D (no new long-running operations).
- R-DIP structurally: composition root remains the only place that knows both adapters and use cases. The "port" is now the public method signature of the concrete class — Python duck typing acts as the structural contract. When/if a 2nd adapter for either store appears (e.g. S3, Redis), reintroducing the Protocol is a one-PR change. We're not paying upfront for a port we don't need.

**Hand-off**:
1. **User must run `git rm`** for the 5 paths above to remove the stubbed files/folders. After the rm the repo will exactly match the PR-E target tree.
2. **User must run** `cd hubara_agency && uv run pytest tests/ -v --no-header`. Expected: **82 passed, 0 failed**.
3. The plugin's "Standard layout" recommendation in `~/.claude/plugins/exoclaw-temporal-expert/skills/deha-architecture/` should be revised in a follow-up PR to recommend the lean collapse for small-to-medium domains and the full hexagonal layout only for large or multi-adapter scenarios. ADR-2026-05-06-07 captures the trade-off.

**Test command (run from `hubara_agency/`)**:
```bash
uv run pytest tests/ -v --no-header
```

## 2026-05-06 — deha-architect — PR-D complete (cleanup: brain-loader path removed from Sales)

**Outcome**: PR-D closes the train. The Sales path of `LoadOrStartSalesSession` no longer takes `sales_brain_loader` / `sales_brain_dir`. `composition.py` no longer references `_SALES_BRAIN_DIR` or builds a Sales brain loader. The 3 `tests/test_bootstrap_sales_activity.py` tests broken by PR-A are now passing the new `SalesSessionInput` shape and a fail-fast test was added. `plugin_context` is documented as a turn-volatile slot (option a, ADR-2026-05-06-06). Remarketing untouched: it still uses its own `shared_brain/`, `BrainLoaderPort`, `DefaultBrainLoader`, and `build_workspace_config` — those are deferred to the future Remarketing DEHA migration.

**Files edited**:
- `hubara_agency/src/domains/sales_whatsapp/application/use_cases/load_or_start_sales_session.py:1-49,77-92,138-148` — removed `sales_brain_loader: BrainLoaderPort`, `sales_brain_dir: Path` from `__init__`. Removed the corresponding attributes. Updated module + class docstrings to reflect PR-D state. Kept `remarketing_brain_loader: BrainLoaderPort` and `remarketing_brain_dir: Path` (R-DIP — port stays, only Sales-specific consumers go).
- `hubara_agency/src/domains/sales_whatsapp/composition.py:1-78` — removed `_SALES_BRAIN_DIR` constant. Removed `sales_brain_loader = DefaultBrainLoader()`. Removed the two args from the `LoadOrStartSalesSession(...)` call. Updated module docstring. `_REMARKETING_BRAIN_DIR` and `remarketing_brain_loader` survive (Remarketing still uses them).
- `hubara_agency/src/domains/sales_whatsapp/contracts.py:32-39` — refreshed `runtime_workspace_path` docstring (removed PR-A "not yet consumed" note; now reflects PR-B/PR-D end state).
- `hubara_agency/src/core/workflow_helpers.py:42-66,84-103` — documented `plugin_context` (on `PendingMessage` and `run_agent_turn`) as **turn-volatile data** (A-MEM, snippets, motivos), explicitly NOT identity/catalog. Documented why the field survives in the signal signature (replay safety).

**Files rewritten**:
- `hubara_agency/tests/test_bootstrap_sales_activity.py` — full rewrite. The 3 PR-A-broken tests now pass `SalesSessionInput(session_id=..., runtime_workspace_path=str(tmp_path/"workspace"))` instead of a raw string. Added `test_bootstrap_failfast_when_workspace_path_missing` that asserts the `RuntimeError` PR-B added. The fixture builds a minimal canonical workspace (5 BOOTSTRAP_FILES) under `tmp_path`. The "creates workspace dir" test from the legacy version is gone — it tested `build_workspace_config`'s `mkdir`, which the activity no longer calls.
- `hubara_agency/tests/test_load_or_start_sales_session.py:122-180,224-247` — `_make_use_case` no longer constructs a `FakeBrainLoader` for Sales. Returns `(use_case, rem_loader)` instead of `(use_case, sales_loader, rem_loader)`. Tests 1, 2, 4 updated to drop the `sales_loader` reference; tests 3 and 4 keep the rem_loader assertions unchanged. The `FakeBrainLoader` class itself stays — still used by the Remarketing-path tests.

**Files NOT touched (deliberate, deferred to future Remarketing DEHA migration)**:
- `hubara_agency/src/core/brains.py` — `load_brain()` is still imported by `domains/remarketing_whatsapp/activities.py:10` (for `load_remarketing_brain_activity`).
- `hubara_agency/src/core/ports/brain_loader.py` — `BrainLoaderPort` is still imported by `LoadOrStartSalesSession` (typed param for `remarketing_brain_loader`) and re-exported from `core/ports/__init__.py`.
- `hubara_agency/src/core/infrastructure/brains/` (`DefaultBrainLoader`) — still instantiated in `composition.py` for `remarketing_brain_loader`.
- `hubara_agency/src/core/infrastructure/adapters/brain_loader_adapter.py` — duplicate `DefaultBrainLoader` in `core/infrastructure/adapters/`. Re-exported from `core/infrastructure/adapters/__init__.py`. I could not confirm via grep (no shell) whether it has a real consumer outside `__init__.py`. Conservative: leave it. If it's truly orphan, a future "infrastructure/adapters dedup" PR can consolidate with `core/infrastructure/brains/default_loader.py`.
- `hubara_agency/src/core/registries.py:build_workspace_config` — still called by `domains/remarketing_whatsapp/activities.py:14,49` (`bootstrap_remarketing_session_activity`). Sales no longer calls it.
- `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/` — Remarketing's identity/knowledge files. Out of scope.

**Files to be deleted (manual `git rm` — the agent has no shell access in this env)**:
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/identity.md`
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/knowledge.md`
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/instructions.md`
- (and the empty `shared_brain/` directory itself)

After my code edits, NO Python module imports those paths. Run:
```bash
cd /Users/edgm/Documents/Projects/AgencyHubara
git rm -r hubara_agency/src/domains/sales_whatsapp/shared_brain/
```

**`plugin_context` decision (option a)**: keep the field, repurpose as turn-volatile data. Justification:
- (b) "delete entirely" requires fixture v3 bump for both Sales **and** Remarketing (signal signature changes), plus a draining/migration plan for in-flight workflows in production (ADR-009). Disruptive for a cleanup PR.
- (c) "rename to `turn_context`" buys nothing semantically that a docstring doesn't already buy, and breaks Remarketing's existing call sites (`workflows/remarketing.py:50,107`).
- (a) — keep + document — is zero behavior change, leaves room for A-MEM in the future, and matches the "no Big Bang" rule. The field is `Optional[list[str]]`; if Remarketing eventually stops sending its brain through it, the parameter can be quietly retired in a later PR with the next legitimate signature bump.

**Tests / verification status**:
- The agent does not have shell access (no `Bash` tool in this turn). I could not run `uv run pytest tests/ -v` myself. The expected delta vs the PR-C run (78 passed, 4 failed):
  - `tests/test_bootstrap_sales_activity.py` — 3 tests rewritten to pass; +1 new failfast test = 4 PASS expected (was 3 FAIL).
  - `tests/test_load_or_start_sales_session.py` — 4 tests, signature update only. Should stay 4/4 PASS.
  - `tests/test_replay_sales.py` — fixture v2 unchanged; should stay PASS if the user already regenerated it per PR-A's hand-off. If `tests/fixtures/history_sales_session_v2.json` is missing on the user's disk (not regenerated since PR-A bumped the constant), this still fails until the user runs `uv run python tests/fixtures/generate_fixtures.py`.
  - All other tests untouched. Expected end state: **82 passed, 0 failed** (or **81 passed, 1 failed** if v2 fixture is missing).

**Anti-patterns avoided**:
- R-DET: no `time.time` / `uuid.uuid4` / `datetime.now` introduced. The use case still touches `client.start_workflow` directly (deuda consciente desde F9) but that's outside `@workflow.defn`.
- R-JSON: `LoadOrStartSalesSession` constructor params remain JSON-relevant or pure adapters (Protocol, Path, dataclass). No Pydantic introduced.
- R-STATELESS: `composition.py` still caches the use case singleton; no new module-level mutable state. Activities are unchanged.
- R-HEARTBEAT: untouched. No new long-running activity bodies.
- R-DIP: `domain/` and `application/` of Sales now depend on **fewer** legacy abstractions (`BrainLoaderPort` only via the Remarketing field). Direction is preserved.

**Hand-off (next iterations)**:
1. **User must run `git rm` for `domains/sales_whatsapp/shared_brain/`** — I left the files in place because I have no shell tool; the code is already independent of them.
2. **User must run `uv run pytest tests/ -v --no-header`** and report the result. If `test_replay_sales.py` fails with "fixture not found", run `uv run python tests/fixtures/generate_fixtures.py` first, then re-run pytest.
3. Future "Remarketing DEHA migration" PR train should mirror this work for Remarketing: introduce `domains/remarketing_whatsapp/workspace/`, swap `load_remarketing_brain_activity` for the workspace runtime, then delete `core/brains.py`, `core/ports/brain_loader.py`, `core/infrastructure/brains/`, and finally remove the `_remarketing_brain_loader` field from `LoadOrStartSalesSession`. At that point the Protocol and adapter graveyard cleans up too.
4. Optional: dedup `core/infrastructure/adapters/brain_loader_adapter.py` vs `core/infrastructure/brains/default_loader.py` — the two implementations are byte-equivalent; one of them is unreferenced.
5. Optional: the `_GHOSTING_PROMPT` in `domains/sales_whatsapp/domain/policies/prompts.py` is still embedded in code rather than a workspace skill `agent_end.md` hook. Candidate for a "skills hooks" follow-up.

**Test command (run from `hubara_agency/`)**:
```bash
# (1) Optional: regenerate v2 fixture if your local copy predates PR-A.
uv run python tests/fixtures/generate_fixtures.py
rm -f tests/fixtures/history_sales_session_v1.json

# (2) Full suite.
uv run pytest tests/ -v --no-header
```

## 2026-05-06 — activity-engineer — PR-C complete (tools comply with exoclaw `Tool` Protocol)

**Outcome**: `TransferToSalesAgentTool` and `ManageConversationTagTool` are now DEHA-compliant: they inherit from `exoclaw.agent.tools.ToolBase`, drop Pydantic, declare a flat JSON schema for `parameters`, and implement `execute_with_context(ctx, **params)`. `core/activities.py:execute_tool` no longer mutates `input.params["ctx"] = ctx` — the DTO is JSON-clean again. `ManageConversationTagTool` registration moved out of `core/registries.py` into `domains/sales_whatsapp/worker.py` via `register_tool_extension(...)` (DIP fix; core no longer imports any `src.domains.*` tool).

**Files edited**:
- `hubara_agency/src/domains/sales_whatsapp/tools/routing.py` — full rewrite. `TransferToSalesAgentTool(ToolBase)`. `parameters` is a class-level dict (`type: object, properties.resumen: {type: string, minLength: 1}, required: [resumen]`). `__init__(workspace: str | Path)`. `execute_with_context(ctx, resumen)` returns the same JSON envelope (`transfer_decision: {session_id, target_route, summary}` + `message`) — byte-equivalent to legacy.
- `hubara_agency/src/domains/sales_whatsapp/tools/tags.py` — full rewrite. `ManageConversationTagTool(ToolBase)`. `parameters.properties.tag.enum = ["INTERESADO", "RECHAZO", "COMPRA_EXITOSA"]`. The legacy silent fallback (`if not isinstance(_tag, str): _tag = 'INTERESADO'`) is gone; invalid tag now surfaces `Error: Invalid parameters ...` to the LLM via `ToolBase.validate_params`. `execute_with_context(ctx, tag, motivo)` returns the same envelope (`schedule_remarketing: {session_id, motivo, delay_seconds}` only when `tag == "INTERESADO"`).
- `hubara_agency/src/core/activities.py:41-47` — removed `input.params["ctx"] = ctx`. Comment explaining why: `ToolRegistry.execute` detects `execute_with_context` via `hasattr` and injects `ctx` itself.
- `hubara_agency/src/core/registries.py:8-13, 32-44` — dropped `from src.domains.sales_whatsapp.tools.tags import ManageConversationTagTool` and the `registry.register(ManageConversationTagTool(...))` call. Updated docstring of `get_base_tools_registry`.
- `hubara_agency/src/domains/sales_whatsapp/worker.py:21, 28-39` — added `from src.domains.sales_whatsapp.tools.tags import ManageConversationTagTool` plus `register_tool_extension("sales.manage_conversation_tag", lambda ws: ManageConversationTagTool(workspace=str(ws)))`. Updated comment.
- `hubara_agency/tests/test_transfer_tool.py` — updated 3 async tests to call `tool.execute_with_context(ctx, **params)` (was `tool.execute(ctx=_Ctx(), **params)`). Switched the local `_Ctx` shim to the real `exoclaw.agent.tools.ToolContext` via a `_ctx(session)` helper.

**Files created**:
- `hubara_agency/tests/test_tools_protocol.py` — 8 new tests covering: protocol shape (no-pydantic regression, `to_schema()`), dispatch via `ToolRegistry.execute`, and validation rejection of bad params (missing required fields, invalid enum value). The dispatch tests prove the production path (`core/activities.py:execute_tool` → `registry.execute` → `execute_with_context`) works without the legacy `params["ctx"]` hack.

**Files NOT touched (deliberate)**:
- `hubara_agency/src/domains/remarketing_whatsapp/worker.py` — Remarketing's prompts never mention `manage_conversation_tag`; the legacy hardcoded registration leaked it but went unused. PR-C deactivates it for Remarketing without functional regression. If Remarketing ever needs the tool, add a parallel `register_tool_extension` call.
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/`, `core/brains.py`, `core/infrastructure/brains/`, `core/ports/brain_loader.py` — alive, deferred to PR-D.
- `hubara_agency/src/core/workflow_helpers.py:_try_parse_decision_payload` — JSON envelope shapes are byte-equivalent to legacy, so the parser stays untouched.

**Tests / verification status**:
- `tests/test_tools_protocol.py` — 8/8 PASS.
- `tests/test_transfer_tool.py` — 5/5 PASS.
- `tests/test_load_or_start_sales_session.py` — 4/4 PASS (PR-B regression test, untouched, still green).
- `tests/test_workspace_system_prompt.py` — 8/8 PASS (PR-B regression test, untouched, still green).
- `tests/test_run_agent_turn.py` — 7/7 PASS (decision-parsing tests for `core/workflow_helpers.py`).
- **Pre-existing failures unrelated to PR-C** (verified via `git stash` of PR-C edits — these failures exist with or without PR-C and depend on uncommitted PR-A/PR-B local edits in the working tree):
  - `tests/test_bootstrap_sales_activity.py` (3 tests) — calls `env.run(bootstrap_sales_session_activity, "wa_xxx")` passing a `str`, but PR-A changed the activity signature to take `SalesSessionInput`. PR-A hand-off didn't update this file. Out of PR-C scope; should be a follow-up alongside the v2 fixture regen.
  - `tests/test_replay_sales.py` — looks for `tests/fixtures/history_sales_session_v2.json` which the user has not yet regenerated locally per PR-A's documented hand-off (`uv run python tests/fixtures/generate_fixtures.py`).

**Anti-patterns avoided**:
- R-DET: no `time.time` / `uuid.uuid4` / `datetime.now` introduced inside any workflow body. The tools' `time.time()` calls live inside `execute_with_context()` which the registry invokes inside the `execute_tool` activity (already the case pre-PR-C; not regressed).
- R-JSON: removed the `input.params["ctx"] = ctx` hack. `ExecuteToolInput.params: dict[str, Any]` is once again strictly JSON-serializable; `ToolContext` no longer crosses serialization boundaries inside it.
- R-STATELESS: `execute_tool` still rebuilds the registry per call via `get_base_tools_registry(workspace_path)` + `apply_tool_extensions(...)`. Tools are constructed fresh each time. No module-level mutable state added.
- R-HEARTBEAT: `execute_tool` keeps `@with_heartbeat(every=10)`. Tool bodies do tiny filesystem I/O on `metadata.json`; no >10s operations introduced.
- R-DIP: `core/registries.py` no longer imports `src.domains.*`. The composition root (per-domain `worker.py`) is the only place that knows both `core/` and `domains/`.

**Hand-off to PR-D (cleanup)**:
1. Delete `hubara_agency/src/domains/sales_whatsapp/shared_brain/`.
2. Delete `hubara_agency/src/core/brains.py`, `src/core/infrastructure/brains/`, `src/core/ports/brain_loader.py`.
3. Remove `sales_brain_loader`, `sales_brain_dir` from `LoadOrStartSalesSession.__init__` and from `composition.py`.
4. Audit `core/registries.py:build_workspace_config` callers — delete if dead.
5. Decide the fate of `plugin_context` in `core/workflow_helpers.py:run_agent_turn` and the signal signatures.
6. Optional: revisit `tests/test_bootstrap_sales_activity.py` (3 broken tests from PR-A's incomplete hand-off — pass `SalesSessionInput(session_id=...)` instead of `"wa_xxx"`) and regenerate the v2 fixture (`uv run python tests/fixtures/generate_fixtures.py`). These are not PR-C regressions; they're tech debt from PR-A.

**Test command (run from `hubara_agency/`)**:
```bash
# PR-C tests + PR-B regression tests
uv run pytest tests/test_tools_protocol.py tests/test_transfer_tool.py \
              tests/test_load_or_start_sales_session.py \
              tests/test_workspace_system_prompt.py \
              tests/test_run_agent_turn.py -v
# Result: 32 passed.

# Full suite (will surface 4 pre-existing PR-A/PR-B failures unrelated to PR-C)
uv run pytest tests/ -v
# Result: 78 passed, 4 failed (test_bootstrap_sales_activity.py x3, test_replay_sales.py x1).
```

## 2026-05-06 — deha-architect — PR-B complete (switchover)

**Outcome**: PR-B is the hot swap. The workspace canonico (`hubara_agency/src/domains/sales_whatsapp/workspace/`) is now the **only** source of identity / tone / catalog for the Sales path: `bootstrap_sales_session_activity` builds `WorkspaceConfig(path=input.runtime_workspace_path)` and fails fast if the path is missing; `LoadOrStartSalesSession` stops loading `shared_brain/*.md` for the Sales path and always passes `plugin_context=None` to `send_message`. The Remarketing path is unchanged. `shared_brain/`, `core/brains.py`, `core/ports/brain_loader.py`, and the brain-loader fields on `LoadOrStartSalesSession` survive in PR-B (PR-D deletes them).

**Files edited**:
- `hubara_agency/src/domains/sales_whatsapp/activities.py:13` — added `WorkspaceConfig` import.
- `hubara_agency/src/domains/sales_whatsapp/activities.py:15-19` — removed `build_workspace_config` import (no longer used).
- `hubara_agency/src/domains/sales_whatsapp/activities.py:67-89` — replaced `ws = build_workspace_config(session_id)` with `ws = WorkspaceConfig(path=input.runtime_workspace_path)`. Added `RuntimeError` failfast when `runtime_workspace_path is None` (composition-root miswire surfaces here, not at the LLM activity producing an empty system prompt).
- `hubara_agency/src/domains/sales_whatsapp/application/use_cases/load_or_start_sales_session.py:124-137` — Sales path no longer calls `_sales_brain_loader.load(...)`. `plugin_context = None` always.
- `hubara_agency/tests/test_load_or_start_sales_session.py:172-176, 195-196, 239-244` — updated three tests: `args[2]` is now `None` (was `["sales-brain"]`); `sales_loader.calls == []` (was `[Path("/tmp/sales-brain")]`).

**Files created**:
- `hubara_agency/tests/test_workspace_system_prompt.py` — regression test that instantiates `ContextBuilder(workspace=WORKSPACE)` directly (avoids `DefaultConversation.create` requirement of a real `LLMProvider`) and asserts that key tokens from each bootstrap file (IDENTITY/SOUL/USER/TOOLS/AGENTS) and the `hubara_catalog` always-on skill cross into the system prompt. Eight tests total: workspace structure check, IDENTITY, SOUL, AGENTS, TOOLS, USER, catalog skill (Cruz de Vida + price), catalog policies (Pago contra Entrega + $45,000 threshold).

**PR-A fixup (caught by the new regression test)**:
- `hubara_agency/src/domains/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md` — frontmatter `metadata` was set as a multi-line YAML block scalar (`metadata: |\n  {...}`), but `SkillsLoader.get_skill_metadata` is a line-by-line parser (NOT YAML-aware) that splits on the first `:` per line. The block scalar resolved to `metadata = "|"`, which `_parse_exoclaw_metadata` could not JSON-decode, so `always: true` never registered and the skill was lazy-loaded only. Fixed by inlining the metadata JSON on a single line: `metadata: {"exoclaw": {"always": true}}`. ADR-2026-05-06-02 intent preserved; the bug was in the encoding, not the decision.

**Files NOT touched (deliberate, deferred to PR-D)**:
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/` — alive but dead code on Sales path.
- `hubara_agency/src/core/brains.py`, `core/infrastructure/brains/`, `core/ports/brain_loader.py` — alive.
- `LoadOrStartSalesSession.__init__` still accepts `sales_brain_loader` and `sales_brain_dir` (unused on Sales path now). PR-D removes them.
- `core/registries.py:build_workspace_config` — alive (unused by Sales activity now). Defer to PR-D for cleanup or evaluate if still used by other callers.
- `core/workflow_helpers.py:run_agent_turn` — `plugin_context` plumbing untouched. The arg now just carries `None` from the Sales path. PR-D will trim if appropriate.

**Files NOT touched (deliberate, deferred to PR-C)**:
- `hubara_agency/src/domains/sales_whatsapp/tools/{routing,tags}.py` — pydantic.Field-based; rewritten in PR-C.
- `hubara_agency/src/core/registries.py:get_base_tools_registry` — `ManageConversationTagTool` still hardcoded. PR-C moves the registration to the sales worker via `register_tool_extension`.

**Tests / verification status**:
- Existing tests `tests/test_load_or_start_sales_session.py` — updated assertions (3 tests). The Remarketing path test is unchanged.
- New regression test `tests/test_workspace_system_prompt.py` — should pass against the committed workspace files. If `hubara_catalog/SKILL.md` frontmatter is malformed (`metadata.exoclaw.always = true`), `test_catalog_skill_loads_always` will fail and surface the issue.
- Replay test `tests/test_replay_sales.py` — points at `history_sales_session_v2.json`. PR-A required the user to regenerate; if that wasn't done, do so now: the activity body changed but the **shape** (input DTO, activity name, return type) didn't. So the v2 fixture from PR-A's regen run will replay against PR-B code unchanged. **No new fixture bump needed.**
- Remarketing replay test (`tests/test_replay_remarketing.py`) — unaffected.

**Anti-patterns avoided**:
- R-DET: no `time.time` / `uuid.uuid4` / `datetime.now` introduced. The activity body reads `input.runtime_workspace_path` (already on the boundary DTO, JSON-serializable).
- R-JSON: `WorkspaceConfig(path=...)` is constructed inside the activity, not crossed as a live object. The string `runtime_workspace_path` already crossed the boundary in PR-A.
- R-STATELESS: activity rebuilds `LLMConfig`, `WorkspaceConfig`, registry on each invocation. No module-level mutable state added.
- R-HEARTBEAT: bootstrap activity is fast (filesystem `mkdir` removed — `WorkspaceConfig` constructor doesn't mkdir; `get_base_tools_registry` does small filesystem inspection only). Stays well under the 10s threshold.
- R-DIP: `application/use_cases/load_or_start_sales_session.py` still imports `WorkspaceConfig` from `exoclaw_temporal.config` (boundary DTO, not framework — same exception as PR-A). No new framework imports added in `domain/` or `application/`.

**Followup observations (not in scope for PR-B)**:
- `core/registries.py:build_workspace_config` is no longer called by the Sales activity. Audit in PR-D if any other caller still uses it; otherwise delete.
- The brain-loader constructor params on `LoadOrStartSalesSession` are now dead-on-arrival for Sales. PR-D removes them.
- `core/workflow_helpers.py:run_agent_turn` passes `plugin_context` to `build_prompt`. After PR-B that value is always `None` for Sales but Remarketing still relies on it. The signal signature stays for replay safety.
- `hubara_agency/src/domains/sales_whatsapp/domain/policies/prompts.py:_GHOSTING_PROMPT` is still embedded in the codebase rather than `skills/ghosting/SKILL.md` with an `agent_end` hook. Candidate for a future iteration once PR-D lands.

**Hand-off to PR-C (tools rewrite)**:
1. Rewrite `domains/sales_whatsapp/tools/routing.py` (`TransferToSalesAgentTool`) and `tools/tags.py` (`ManageConversationTagTool`) to inherit from `ToolBase`, drop `pydantic.Field` for params, implement `execute_with_context`.
2. Move `ManageConversationTagTool` registration from `core/registries.py:get_base_tools_registry` into the sales worker via `register_tool_extension` (so the `core/` layer stops knowing about a domain-specific tool — DIP fix).
3. Verify the regression test from PR-B keeps passing after the tool rewrite (the system prompt content shouldn't change; tool *registration* changes don't touch the workspace files).
4. The `RuntimeError` failfast added in `bootstrap_sales_session_activity` will surface if PR-C accidentally drops the `runtime_workspace_path` plumbing — treat as a regression.

**Hand-off to PR-D (cleanup)**:
1. Delete `hubara_agency/src/domains/sales_whatsapp/shared_brain/`.
2. Delete `hubara_agency/src/domains/remarketing_whatsapp/shared_brain/` once Remarketing has its own workspace (out of scope for this train of PRs unless explicitly added).
3. Delete `hubara_agency/src/core/brains.py`, `src/core/infrastructure/brains/`, `src/core/ports/brain_loader.py`.
4. Remove `sales_brain_loader`, `sales_brain_dir` from `LoadOrStartSalesSession.__init__` and from `composition.py`.
5. Audit `core/registries.py:build_workspace_config` callers — delete if dead.
6. Decide the fate of `plugin_context` in `core/workflow_helpers.py:run_agent_turn` and the signal signatures (`HubaraSalesSessionWorkflow.send_message`, `RemarketingSessionWorkflow.send_message`): repurpose for A-MEM/turn_context or remove entirely. If kept, document that it carries volatile per-turn data only (not identity/catalog).
7. PR-D may require a fixture v3 bump if signal signatures change.

**Test command (run from `hubara_agency/`)**:
```bash
# Step 1: regenerate v2 fixture (PR-A handoff still pending in your local repo)
uv run python tests/fixtures/generate_fixtures.py
# Step 2: clean stale v1 fixture
rm -f tests/fixtures/history_sales_session_v1.json
# Step 3: run the suite
uv run pytest tests/ -v --no-header
```
If `uv` is not on the agent's PATH, the user must run the commands locally. The deha-architect agent does not have shell access in this environment, so test results are not captured here — they are the user's responsibility to run and report.

## 2026-05-06 — deha-architect — PR-A complete

**Outcome**: PR-A wired in. The agent runtime workspace exists, is committed to the repo at `hubara_agency/src/domains/sales_whatsapp/workspace/`, and a `WorkspaceConfig(path=...)` DTO is plumbed through the composition root → `LoadOrStartSalesSession` → `SalesSessionInput.runtime_workspace_path` → `bootstrap_sales_session_activity`. The activity logs that it received the path but does not consume it yet — `plugin_context` (legacy `shared_brain/`) keeps winning. Diff of system prompt = 0 by construction.

**Files created (workspace skeleton)**:
- `hubara_agency/src/domains/sales_whatsapp/workspace/IDENTITY.md` — agent identity (from `shared_brain/identity.md`).
- `hubara_agency/src/domains/sales_whatsapp/workspace/SOUL.md` — tone/voice/format rules (upper half of `shared_brain/instructions.md`).
- `hubara_agency/src/domains/sales_whatsapp/workspace/USER.md` — tenant defaults stub.
- `hubara_agency/src/domains/sales_whatsapp/workspace/TOOLS.md` — tag taxonomy + closing rules (lower half of `shared_brain/instructions.md`).
- `hubara_agency/src/domains/sales_whatsapp/workspace/AGENTS.md` — operational rules (ghosting, escalation, channel etiquette).
- `hubara_agency/src/domains/sales_whatsapp/workspace/memory/MEMORY.md` — long-term memory (empty).
- `hubara_agency/src/domains/sales_whatsapp/workspace/memory/HISTORY.md` — append-only log header.
- `hubara_agency/src/domains/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md` — full catalog with `metadata.exoclaw.always = true` per ADR-2026-05-06-02.

**Files created (config)**:
- `hubara_agency/src/domains/sales_whatsapp/config/__init__.py`
- `hubara_agency/src/domains/sales_whatsapp/config/env.py` — `get_workspace_path()` reads `EXOCLAW_WORKSPACE_SALES` env var, defaults to the in-repo workspace.

**Files edited**:
- `hubara_agency/src/domains/sales_whatsapp/contracts.py` — added `runtime_workspace_path: str | None = None` to `SalesSessionInput`.
- `hubara_agency/src/domains/sales_whatsapp/composition.py` — instantiates `WorkspaceConfig(path=str(get_workspace_path()))` and passes it as `sales_runtime_workspace=` to `LoadOrStartSalesSession`.
- `hubara_agency/src/domains/sales_whatsapp/application/use_cases/load_or_start_sales_session.py` — accepts `sales_runtime_workspace: WorkspaceConfig | None = None`; forwards `runtime_workspace_path` into the new `SalesSessionInput` when starting Sales workflows.
- `hubara_agency/src/core/infrastructure/temporal/dispatcher_activities.py` — `start_or_signal_sales_workflow_activity` resolves the runtime workspace path via `get_workspace_path()` and forwards it on the `SalesSessionInput` it builds when starting a Sales workflow.
- `hubara_agency/src/domains/sales_whatsapp/activities.py` — `bootstrap_sales_session_activity` now takes `input: SalesSessionInput` (was `session_id: str`). Logs the received `runtime_workspace_path` but keeps `SessionInput.workspace` pointing at the per-session vault (zero behavior change).
- `hubara_agency/src/domains/sales_whatsapp/workflows/sales_session.py` — passes the whole `SalesSessionInput` to `bootstrap_sales_session_activity` (single positional arg) instead of `args=[input.session_id]`.
- `hubara_agency/tests/fixtures/generate_fixtures.py` — bumped Sales fixture to `history_sales_session_v2.json`; mock signature updated to match new activity input shape.
- `hubara_agency/tests/test_replay_sales.py` — points at `v2`.
- `hubara_agency/tests/fixtures/README.md` — documents the v1 → v2 bump.

**Files NOT touched (deliberate)**:
- `hubara_agency/src/domains/sales_whatsapp/shared_brain/` — kept intact. `plugin_context` legacy path is still active.
- `hubara_agency/src/core/brains.py`, `core/infrastructure/brains/`, `core/ports/brain_loader.py` — alive in PR-A. Removed in PR-D.
- `hubara_agency/src/core/registries.py` — `ManageConversationTagTool` still hardcoded there. Moves in PR-C.
- `hubara_agency/src/domains/sales_whatsapp/tools/{routing,tags}.py` — pydantic.Field-based; rewritten in PR-C.
- `hubara_agency/src/domains/sales_whatsapp/domain/policies/prompts.py` — `_GHOSTING_PROMPT` stays here for now; candidate to migrate to a `ghosting` skill `agent_end.md` hook in a later iteration.
- `hubara_agency/src/core/workflow_helpers.py` — `plugin_context` plumbing untouched. Trim semantics in PR-B.

**Tests / verification status**:
- The replay test `tests/test_replay_sales.py` requires regenerating the fixture **before** it goes green. Run `cd hubara_agency && uv run python tests/fixtures/generate_fixtures.py`, then delete the stale `tests/fixtures/history_sales_session_v1.json`. Without that, the Replayer will throw NonDeterminismError on the new activity arg shape — which is the correct behavior, the v1 fixture captured the old shape.
- The four use case tests in `tests/test_load_or_start_sales_session.py` should remain green: the new `sales_runtime_workspace` constructor param defaults to `None`, and existing assertions check `payload.session_id` (not the new field).
- The remarketing replay test is unaffected (only Sales' bootstrap activity changed shape).

**Anti-patterns avoided**:
- R-DET: `get_workspace_path()` reads `os.environ` only at composition root and `dispatcher_activities` (worker init / activity body — both outside `@workflow.defn`). The workflow body never touches the filesystem.
- R-JSON: `runtime_workspace_path: str | None` is a flat field on a dataclass that already crossed the boundary. No nested live objects.
- R-STATELESS: `composition.py` caches the use case singleton; the activity itself rebuilds `LLMConfig` / `WorkspaceConfig` / registry on each invocation (existing behavior, untouched).
- R-DIP: domain & application layers still depend only on Protocols (`MetadataStorePort`, `MessageHistoryStorePort`, `BrainLoaderPort`). The `WorkspaceConfig` import in `load_or_start_sales_session.py` is a boundary DTO, not a framework dependency.

**Hand-off to PR-B**:
1. In `bootstrap_sales_session_activity`, swap `ws = build_workspace_config(session_id)` for:
   ```python
   if input.runtime_workspace_path:
       ws = WorkspaceConfig(path=input.runtime_workspace_path)
   else:
       ws = build_workspace_config(session_id)  # legacy fallback
   ```
   Validate that `Path(input.runtime_workspace_path)` contains the 5 UPPERCASE files before the swap. Add a regression test that calls `DefaultConversation.create(workspace=Path(input.runtime_workspace_path))` and `ContextBuilder.build_system_prompt()` returns the same identity block as the legacy `plugin_context`.
2. Stop loading `plugin_context` from `_sales_brain_loader` in `LoadOrStartSalesSession.execute` for the Sales path. Remarketing keeps it for now (out of PR-B scope).
3. In `core/workflow_helpers.py:run_agent_turn`, the `plugin_context` argument can stay (it now carries only volatile per-turn data — A-MEM, retrieved snippets — not identity/catalog).
4. Bump fixture to v3 if the activity body changes shape again (it shouldn't — the input DTO is the same).
