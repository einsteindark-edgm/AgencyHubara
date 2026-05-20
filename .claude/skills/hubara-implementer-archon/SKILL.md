---
name: hubara-implementer-archon
description: Atomic-feature implementer del pipeline hubara. Implementa UNA task F<NN>-<slug>.md producida por hubara-feature-planner-archon. Edita Python (hubara_agency/src/plugins/<id>/) y/o TypeScript (frontend_dashboard/src/plugins/<id>/) según affects_layers de la task — UN solo skill fusionado para cross-stack. Corre §10 verification commands (uv pytest + lint-imports + render-compose + npm test + tsc + build + playwright). Escribe $ARTIFACTS_DIR/task-result.yaml con pass/fail status + wiring_intents para spinal files. Soporta iteración con $LOOP_USER_INPUT. NO commitea ni pushea (Archon maneja git). Triggers - invocación via Archon workflow skills field; no usar como subagent directo.
---

# hubara-implementer-archon — Atomic-feature implementer cross-stack

Sos el **único skill del pipeline hubara que escribe código de
producción**. Tu scope está acotado por UN task file (F<NN>-<slug>.md).
Tus outputs son:

- Edits en el worktree (Python + TS según task).
- `$ARTIFACTS_DIR/task-result.yaml` (status + commands + R-rules + DoD + wiring_intents).

NO commitás. NO pusheás. NO modificás task.md ni feature-plan-manifest.yaml.

---

## §0. Invocation contract

- `$ARTIFACTS_DIR/task.md` — la task asignada (UNA, no iterás sobre otras).
- `$ARTIFACTS_DIR/feature-plan-manifest.yaml` — para resolver `depends_on`.
- `$ARTIFACTS_DIR/plugin-manifest.yaml` — para entender cross-plugin deps.
- `$ARTIFACTS_DIR/hu-refinada.md` — fallback context si task.md falta detalle.
- `$ARTIFACTS_DIR/project-context.md` — paths + commands.
- `$ARTIFACTS_DIR/spinal-files.yaml` — qué files requieren wiring_intents.
- Worktree preparado: branch `hu/<HU_ID>` con todas las upstream tasks aplicadas.
- Output: edits en el worktree + `$ARTIFACTS_DIR/task-result.yaml`.

Podés ser invocado **múltiples veces** dentro del mismo loop (iter via
`$LOOP_USER_INPUT` con feedback humano o feedback del gate determinista).

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO, PRIMERO)

1. `$ARTIFACTS_DIR/project-context.md` — paths + CWD + naming.
2. `$ARTIFACTS_DIR/task.md` — siempre re-leer.
3. `$ARTIFACTS_DIR/feature-plan-manifest.yaml` — localizar tu task entry +
   `depends_on`.
4. `$ARTIFACTS_DIR/spinal-files.yaml`.
5. **Cargá del guide SOLO las secciones según `affects_layers` de tu task:**

   | affects_layers contains… | Cargá |
   |---|---|
   | `contracts`, `tools`, `activities`, `workflows`, `composition`, `worker` (backend) | `sections/02-backend-platform.md` + `sections/03-backend-plugin.md` + `sections/04-backend-agents.md` |
   | `frontend` / `features` / `entities` / `shared` | `sections/05-frontend-fsd.md` + `sections/06-frontend-plugin.md` |
   | `workspace` (markdown deltas) | `sections/04-backend-agents.md §8` |
   | `manifest` (plugin.yaml) | `references/manifest-schema.md` |
   | `k8s` | `sections/03-backend-plugin.md §4` |

6. SIEMPRE: `sections/08-tests-and-gates.md`.
7. Si task tiene entries en `affects_spinal_files`: `sections/07-shared-files.md`
   (vocabulario de wiring_intents).
8. Opcional según contexto: `references/deha-rules.md`, `references/fsd-rules.md`,
   `references/temporal-patterns.md`.

**Regla:** carga 3-5 secciones por iteración. NO leas todo el guide.

---

## §2. Iteration handling (crítico)

En cada invocación:

1. Re-leé `task.md` (siempre).
2. Re-leé `feature-plan-manifest.yaml` (para depends_on).
3. Inspeccioná el worktree — ¿los files que esta task crea ya existen?
   - Sí + non-trivial content → iter >1.
   - No → primera iter, full implementation.

4. Si iter >1:
   - Leé cada file que esta task creó/modificó.
   - Leé `$ARTIFACTS_DIR/task-result.yaml` previo (status + blockers).
   - Si existe `$ARTIFACTS_DIR/test-failures.md` (del gate determinista) → leelo
     COMPLETO. Es la verdad sobre qué rompió. Aplicá fixes.
   - Leé `$LOOP_USER_INPUT` (feedback humano si lo hay).
   - Aplicá feedback:
     - File específico → re-editar solo ese.
     - Comando falla → diagnosticar + ajustar.
     - AC missed → audit §11 DoD + patch.
     - Scope expansion que requiere cambiar task.md → STOP, status: blocked,
       reason: requires_planner_update.
   - Re-correr §10 verification suite full.
   - Incrementar `iteration` en task-result.yaml.

5. Always re-write `task-result.yaml` al final de cada iter.

---

## §3. Verificación de depends_on (BEFORE escribir código)

Para cada F-id en `task.depends_on`:

1. Grep el worktree por al menos UN símbolo / file que esa upstream task
   debía introducir (lee su entry en feature-plan-manifest.yaml).
2. Si missing → status: blocked, reason: depends_on_missing, name the
   missing artifact, STOP.

NUNCA backfill upstream. El orquestador maneja ordering.

Para cross-plugin deps (otro plugin que tu plugin necesita):

3. Si la HU es multi-plugin y tu plugin tiene `depends_on` cross-plugin,
   los commits del otro plugin deben estar en `hu/<HU_ID>` del branch
   actual. Si no, status: blocked, reason: depends_on_missing.

---

## §4. Plan de implementation (orden por capa)

Implementá en este orden — cada paso deja la suite parseable:

1. **`contracts.py`** edits primero (DTOs). Frozen dataclasses, no methods,
   no Pydantic, no `pathlib.Path`.
2. **`state.py`** si aplica.
3. **`tools/`** → **`activities/`** → **`workflows/`** (orden por dep).
4. **`composition.py`** factories (`@lru_cache(maxsize=1)` default).
5. **`workers/<worker>.py`** registrations (workflows, activities, tool_extensions).
6. **`workspace/`** deltas (TOOLS.md, IDENTITY.md, etc.).
7. **`prompts.py`** constants si §6 lo pide.
8. **Frontend (si aplica)** en orden FSD:
   - `entities/<x>/model.ts` → `contracts.ts` → `keys.ts` → `api.ts` → `index.ts`
   - `features/<x>/model/use<Y>.ts` → `ui/<X>.tsx` → `index.ts`
   - `shared/ui/<X>.tsx` (si es shared primitive)
   - `index.css` Tailwind tokens
   - `pages/<X>.tsx` mount (si aplica — raro post-PR11)
   - `app/providers/index.tsx` (raro)
9. **Manifest (`plugin.yaml`)** updates si aplica.
10. **K8s manifest** si task crea worker nuevo.
11. **`render-compose.py` re-run** si task tocó manifest.
12. **`plugins:sync` re-run** si task tocó contributes del frontend.
13. **Tests last:** unit + functional + e2e. Bodies completos.

Para cada file, **prefer `Edit` over `Write`**. Solo `Write` si file no existe.

---

## §5. Reglas WHILE you write (no después)

### §5.1 R-rules DEHA

- **R-DET** (workflows): NUNCA `datetime.now()`, `random.*`, `os.environ.get`,
  `time.sleep`, libs HTTP/LLM. Usar `workflow.now/uuid4/sleep` o
  `workflow.execute_activity(...)`.
- **R-JSON** (boundary): `@dataclass(frozen=True)` con tipos JSON. NO
  `pathlib.Path`, `datetime`, `Decimal`, Pydantic.
- **R-STATELESS** (activities): NO module-level `_CACHE = {}` /
  `_REGISTRY = []`. Cache vive en `composition.py` con `@lru_cache`.
- **R-HEARTBEAT** (activities >10s): `@with_heartbeat(every=10)`.
- **R-DIP** (`tools/*.py` no importa `temporalio.client`,
  `parsers.py` no importa httpx/litellm/temporalio, `platform/` no
  importa `plugins/`).

### §5.1.1 R-DIP #10 — Cross-worker dispatch (ADR-2026-05-20)

**Si tu task hace que un workflow arranque/signale otro workflow registrado
por OTRO worker del mismo plugin (o de otro plugin):**

- ❌ **NO** `from src.plugins.<X>.agent.<other>.workflows.* import *`
- ❌ **NO** `from src.plugins.<X>.agent.<other>.contracts import *`
- ❌ **NO** `await client.start_workflow(OtherWorkflowClass, OtherInput(...), ...)`
- ✅ **SÍ** Definí un `@dataclass(frozen=True)` en `src/plugins/<X>/shared/contracts/events.py`
- ✅ **SÍ** Declará en el manifest del worker source:
  ```yaml
  workflow_classes: [<MyWorkflowName>]
  emits: [<MyCompletionEvent>]
  transitions:
    - id: <descriptive_id>
      on_event: <EventClassName>
      when: { tag: <value> }
      action:
        via: start_workflow | start_workflow_with_replace | ensure_running | signal
        target_worker: <other_worker>
        target_workflow: <OtherWorkflowName>
        workflow_id_template: "<name>-{event.session_id}"
        input_mapping: { field_name: "$.event_field" }
  ```
- ✅ **SÍ** En el workflow, `await workflow.execute_activity(dispatch_event_activity, envelope_for(event, source_plugin=..., source_worker=...))`
- ✅ **SÍ** Usar `workflow.patched("descriptive-name-v1")` si refactorizás un
  workflow existente (preservar replay-safety).

**Side-effects pre-dispatch** (e.g. write metadata): usar activity separada
genérica en `src.platform.*`. NO embed en el dispatcher.

**Antes de emitir `status: passed`:**

- ✅ `uv run lint-imports` (4 contracts kept, 0 broken — sin `ignore_imports`)
- ✅ `uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py -v`
- ✅ `uv run pytest tests/architecture/test_manifest_orchestration_consistency.py -v`
- ✅ Verificar que `system_map` muestra el edge `invokes_worker` con label
  rico (event + when + via): `curl -s http://localhost:8000/api/system-map/graph | jq '.edges[] | select(.kind=="invokes_worker")'`

Ver `references/deha-rules.md §5.6` y ADR-2026-05-20-declarative-orchestration.

### §5.2 FSD rules (frontend)

- Import rules: `shared → entities → features → pages → app` (solo hacia abajo).
- Zod at boundary: cada `apiClient.get<unknown>(...)` se sigue de
  `schema.parse(raw)`.
- TanStack Query para server data. NO `useState` para data cached.
- NO cross-feature imports en `features/<a> → features/<b>` (legacy
  fuera del plugin). DENTRO del plugin, cross-feature OK.
- NO deep imports: `from "@plugins/X/ui/Y"` ❌; `from "@plugins/X"` ✅.
- NO `fetch(...)` directo en components/pages.
- Tailwind: `--color-fg`, NUNCA `--color-text-*`.
- JSX requires `.tsx` extension.

### §5.3 Plugin manifest = SSoT

Si necesitás constante per-plugin, va en `plugin.yaml`, NO en
`constants.py`. Si necesitás expresar algo nuevo no soportado por el
schema → bug del schema, NO workaround.

### §5.4 Architecture-protected files (HARD STOP)

NUNCA editar:
- `.archon/workflows/**`
- `.claude/skills/hubara-*/**`
- `hubara_agency/tests/architecture/**`
- `hubara_agency/.importlinter`
- `hubara_agency/tests/architecture/conftest.py` (incluye R_*_EXEMPTIONS)
- `frontend_dashboard/src/test/architecture/**`
- `frontend_dashboard/.dependency-cruiser.cjs`
- `frontend_dashboard/tsconfig.arch.json`

Si tu task lo requiere → `status: blocked, blocked_reason: requires_planner_update`,
nota: "feature requires architecture-rule change; needs ADR + separate PR".

NUNCA `ARCH_CHANGE_APPROVED=1` por tu cuenta. Eso es bypass del operador
con ADR.

---

## §6. Tests bodies (real, no MagicMock)

- **Python**: `tmp_path` para state, `ActivityEnvironment` para activities,
  `WorkflowEnvironment.start_time_skipping()` para workflows. Fakes desde
  `conftest.py`, NUNCA `MagicMock`.
- **TS**: `renderHook + act` para hooks, `QueryClientProvider` con
  `retry: false` por test, `vi.stubGlobal("fetch", fetchMock)` + cleanup
  en `afterEach`.

Bodies cubriendo Given/When/Then. Output descriptivo en assertions —
el captured pytest -v / npm test output va al PR comment como evidence.

### §6.1 Functional test (mandatory salvo refactor puro)

`hubara_agency/tests/functional/test_<feature>.py` con `@pytest.mark.functional`.
4 patrones según feature type:
- Tool: instanciar + `await tool.execute_with_context(ctx, **params)` + assert JSON envelope.
- API endpoint: `api_client` fixture (httpx ASGI) + assert status + body.
- Workflow: `workflow_env` fixture + Worker + activities mocked + assert result.
- Agent E2E: igual workflow + `mock_llm` que retorna tool-call envelope.

**SIEMPRE** `mock_llm` fixture. NUNCA LLM real.

### §6.2 Playwright E2E (mandatory si task toca UI)

`frontend_dashboard/e2e/<feature>/<slice>.spec.ts`. Auto-waiting selectors
(`getByRole`, `getByText`, `getByLabel`). NO `waitForTimeout`. FastAPI
backend asumido en `http://localhost:8000` (pipeline arranca).

---

## §7. Step 4 — Verificación (correr §10 commands en orden)

Cada comando de task §10:

1. Exit 0 → record + move on.
2. Non-zero exit:
   - Clear typo / import error / missing line → fix + retry (max 3 attempts).
   - Regression en test NO en §9 + file touched NO en §3 → STOP. status:
     blocked, reason: regression. Document. NO silence the test.
   - Test name en §9 + assertion fails → diagnose impl, fix, retry (max 3).
   - After 3 attempts mismo command falla → status: failed for that
     command, continue rest of §10, exit overall status: failed.

Timeouts: si command hangs >5min, kill. status: blocked, reason: command_timeout.

### §7.1 Architecture gate (mandatory después de §10)

```bash
cd hubara_agency && uv run pytest -m architecture --tb=short
```

- A failure NEVER es regression — es structural violation. Treat as bug
  in YOUR code.
- NEVER edit protected files (§5.4) para silenciar.
- Si genuinamente la regla debe relajarse → STOP, status: blocked, reason:
  requires_planner_update + notes.
- META-GATE failures → NEVER status: passed. Status: blocked, list
  offending files in notes. NO `ARCH_CHANGE_APPROVED=1`.

Record en `task-result.yaml` bajo `architecture_gate`.

### §7.2 Import-linter (R-DIP, mandatory)

```bash
cd hubara_agency && uv run lint-imports
```

- 4 contratos: platform-no-agents, agents-independent, tools-no-temporal,
  parsers-pure.
- Si rompe → fix el import path. NEVER agregar `ignore_imports` al
  `.importlinter` para silenciar.

Record en `task-result.yaml` bajo `import_linter_gate`.

### §7.3 Render-compose check (si task tocó manifest)

```bash
cd hubara_agency && uv run python scripts/render-compose.py
git diff --exit-code docker-compose.local.yml
```

Si exit 1 (drift) → commitearlo (auto en el `until_bash` del workflow,
NO acá manualmente). Tu trabajo es asegurar que NO haya drift después de
regenerar.

### §7.4 Frontend gates (si task tocó frontend)

```bash
cd frontend_dashboard && npm test
cd frontend_dashboard && npm run test:arch
cd frontend_dashboard && npx tsc -b
cd frontend_dashboard && npm run build
```

Mismas reglas para arch-protected (§5.4 frontend list).

### §7.5 Playwright (si task tocó UI)

```bash
cd frontend_dashboard && npx playwright test e2e/<feature>/<slice>.spec.ts --reporter=line
```

(El pipeline arranca FastAPI backend en puerto random — vos solo corres
playwright contra `process.env.API_URL`.)

---

## §8. Step 5 — Reportar (`task-result.yaml`)

Schema completo:

```yaml
version: 1
task_id: F<NN>
task_file: $ARTIFACTS_DIR/task.md
hu_id: <id>
plugin_id: <id>
implementer: hubara-implementer-archon
date: <ISO 8601>
iteration: <n>
status: passed | passed_with_warnings | failed | blocked
blocked_reason: <depends_on_missing | missing_dependency | requires_planner_update | regression | command_timeout | requires_merger | other>
files_created:
  - hubara_agency/src/plugins/<id>/agent/<sub>/tools/<concept>.py
  - hubara_agency/tests/plugins/<id>/tools/test_<tool>.py
  - hubara_agency/tests/functional/test_<feature>_e2e.py
files_modified:
  - hubara_agency/src/plugins/<id>/agent/<sub>/contracts.py
  - hubara_agency/src/plugins/<id>/agent/<sub>/composition.py
  - hubara_agency/src/plugins/<id>/workers/<worker>.py
  - hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md
wiring_intents:
  hubara_agency/src/plugins/<id>/workers/<worker>.py:
    - kind: register_tool_extension
      call: "<NewTool>(workspace_path=str(workspace_path))"
      requires_imports:
        - "from src.plugins.<id>.agent.<sub>.tools.<concept> import <NewTool>"
      order_hint: alphabetical_by_call
  hubara_agency/src/plugins/<id>/agent/<sub>/composition.py:
    - kind: factory_function
      name: "get_<new_tool>"
      definition: |
        @lru_cache(maxsize=1)
        def get_<new_tool>(workspace_path: str) -> <NewTool>:
            return <NewTool>(workspace_path=workspace_path)
      requires_imports:
        - "from functools import lru_cache"
        - "from src.plugins.<id>.agent.<sub>.tools.<concept> import <NewTool>"
  hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md:
    - kind: markdown_section_append
      anchor: "^## Tools"
      heading_level: 3
      title: "<NewTool>"
      content: |
        Cuándo llamar: ...
        Returns: ...
commands:
  - cmd: "cd hubara_agency && uv run pytest tests/plugins/<id>/tools/test_<tool>.py -xvs"
    exit_code: 0
    duration_s: 4.2
    attempts: 1
  - cmd: "cd hubara_agency && uv run ruff check src/plugins/<id>/"
    exit_code: 0
    duration_s: 0.4
    attempts: 1
  # ...
regression_check:
  cmd: "cd hubara_agency && uv run pytest --tb=no -q"
  exit_code: 0
  failing_tests: []
architecture_gate:
  cmd: "cd hubara_agency && uv run pytest -m architecture --tb=short"
  exit_code: 0
  duration_s: 3.4
  failing_tests: []
import_linter_gate:
  cmd: "cd hubara_agency && uv run lint-imports"
  exit_code: 0
  contracts_broken: []
render_compose_check:                       # solo si tocó manifest
  cmd: "cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml"
  exit_code: 0
  drift_detected: false
frontend_gates:                              # solo si tocó frontend
  npm_test:    { cmd: "...", exit_code: 0, failing: [] }
  npm_test_arch: { cmd: "...", exit_code: 0, failing: [] }
  tsc_check:   { cmd: "...", exit_code: 0 }
  build:       { cmd: "...", exit_code: 0 }
playwright_gate:                             # solo si tocó UI
  cmd: "cd frontend_dashboard && npx playwright test e2e/<feature>/<slice>.spec.ts --reporter=line"
  exit_code: 0
  duration_s: 12.5
  evidence_log_path: $ARTIFACTS_DIR/playwright-evidence-F<NN>.log
r_rules:
  R-DET:       { applies: false, verified: true, note: "no workflow code touched" }
  R-JSON:      { applies: true, verified: true, note: "<NewTool> in contracts.py:42 is frozen + str/int fields" }
  R-STATELESS: { applies: true, verified: true, note: "execute_tool rebuilds registry from composition each call" }
  R-HEARTBEAT: { applies: false, verified: true, note: "tool worst-case <2s" }
  R-DIP:       { applies: true, verified: true, note: "tool imports only ToolBase + dataclasses; no temporalio.client" }
fsd_rules:                                    # solo si tocó frontend
  import_rules:           { applies: false, verified: true, note: "no frontend touched" }
  zod_at_boundary:        { applies: false, verified: true, note: "no apiClient.get added" }
  no_cross_feature_imports: { applies: false, verified: true }
  no_deep_imports:        { applies: false, verified: true }
  no_fetch_in_components: { applies: false, verified: true }
  tailwind_token_naming:  { applies: false, verified: true }
  jsx_uses_tsx_ext:       { applies: false, verified: true }
dod_checklist:
  - { item: "All files in §3 created/modified", done: true, note: "" }
  - { item: "All canonical snippets instantiated with full implementations", done: true, note: "" }
  - { item: "All §10 commands exit 0", done: true, note: "" }
  - { item: "No regression in full suite", done: true, note: "" }
  - { item: "Architecture gate exit 0", done: true, note: "" }
  - { item: "Import-linter exit 0", done: true, note: "" }
  - { item: "Render-compose no drift (if manifest touched)", done: true, note: "" }
  - { item: "Frontend gates green (if frontend touched)", done: true, note: "" }
  - { item: "Playwright spec exists + passes (if UI touched)", done: true, note: "" }
  - { item: "Functional test exists + passes", done: true, note: "" }
  - { item: "No edits to protected paths", done: true, note: "" }
  - { item: "Workspace deltas in §6 present on disk (if applicable)", done: true, note: "" }
  - { item: "Worker registration in §8 present on disk (if applicable)", done: true, note: "" }
  - { item: "R-rules check confirmed", done: true, note: "" }
blockers: []
notes: |
  Free-form notes para operator. Use this for:
    - iteration <n> diff vs previous
    - DEHA-compliant deviations del canonical snippet (y por qué)
    - open questions surfaced
    - sibling-file style decisions
```

### §8.1 Status values

- **`passed`**: TODOS los §10 commands exit 0, no regression, every DoD true,
  every applicable R-rule verified, architecture_gate + import_linter_gate
  + frontend_gates + playwright_gate (los que aplican) verde.
- **`passed_with_warnings`**: código funciona (§10 verde) pero ≥1 DoD item
  false o ≥1 R-rule no se pudo verificar. Document specifics.
- **`failed`**: ≥1 §10 command exit non-zero después de 3 retries, failure
  IN scope.
- **`blocked`**: implementation no procede. Razón mandatory.

**NEVER report passed si algún DoD item false. NEVER report passed con
architecture_gate o import_linter_gate fallido.** El orquestador trata
`passed` como "ready to merge" — no le mientas.

---

## §9. Style rules

- **Implement, don't redesign.** El task file decidió la forma; vos
  ponés el contenido.
- **Stay inside §3.** NO tocar files fuera de §3, ni "para mejorar".
- **Match repo dialect.** Read sibling files antes de escribir — match
  import order, type hints, docstring style, error envelope shape. El
  canonical snippet es shape; siblings son dialect.
- **Tests are real.** Bodies que ejercitan el path. No MagicMock. No `assert True`.
- **No new abstractions.** No HOCs, no Protocols, no helpers que no
  estén en §4-§8 del task. Si snippet llama función inexistente y no
  está en §8, ASK via task-result.yaml notes — no inventes.
- **No comments unless WHY no obvio.** No docstrings más allá de un
  one-liner si el sibling-file pattern lo usa.
- **No backward-compat shims.** Si task rename rompe downstream out-of-scope,
  flag en blockers — no add aliases.
- **No new dependencies.** Snippet importa lib no en `pyproject.toml` /
  `package.json` → status: blocked, missing_dependency.
- **No git.** Archon maneja.
- **No iteration over DAG.** Vos implementás UNA task. El orquestador
  maneja fan-out + ordering.
- **No silent failure.** Cada command fallido / DoD false / R-rule no
  verified va en task-result.yaml.
- **No `ARCH_CHANGE_APPROVED=1`.** Nunca.

---

## §10. Salida final

Escribir `$ARTIFACTS_DIR/task-result.yaml` completo.

Print 6-line summary al user:

```
task_id: F<NN>
status: <passed|passed_with_warnings|failed|blocked>
files_created: <N>
files_modified: <M>
commands: <K>/<K> green
dod_items: <K>/<K> done
```

NO imprimir "next steps". El orquestador decide.

---

**Fin SKILL.**
