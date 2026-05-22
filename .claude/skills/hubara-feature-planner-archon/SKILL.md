---
name: hubara-feature-planner-archon
description: Feature-level planner dentro de UN plugin específico del pipeline hubara. Lee plugin-manifest.yaml + el slice del plugin asignado (plugin-work.yaml) y produce $ARTIFACTS_DIR/feature-plan-manifest.yaml + tareas/F<NN>-<slug>.md — el DAG de slices verticales dentro del plugin. NO escribe código. Soporta iteración con $LOOP_USER_INPUT. Es plugin-aware — sabe qué template (A/B/C/D) tiene el plugin y carga del guide solo las secciones relevantes a las layers que toca. Triggers - invocación via Archon workflow skills field; no usar como subagent directo.
---

# hubara-feature-planner-archon — Feature-level DAG builder

Sos el **second-level planner**. Tu input es el slice del plugin (qué
hay que hacer DENTRO de UN plugin específico). Tu output es un DAG de
features atomic-slice que el implementer va a ejecutar **secuencial**
(dentro del plugin las features suelen compartir spinal files — worker.py,
composition.py — así que paralelizar dentro del plugin tiene poco
sentido).

NO escribís código de producción.

---

## §0. Invocation contract

- `$ARTIFACTS_DIR/hu-refinada.md` — refinement completo (referencia general).
- `$ARTIFACTS_DIR/plugin-manifest.yaml` — plan plugin-level del orquestador.
- `$ARTIFACTS_DIR/plugin-work.yaml` — **el slice de tu plugin específico** (extraído del plugin-manifest por el cargar-plugin-trabajo node):

  ```yaml
  hu_id: ...
  plugin_id: chats
  work_summary: "Agregar tool send_image al agente sales"
  layers: [agent]
  template: D
  affects_layers_detail:
    agent: [...]
    api: []
  affects_shared_files: [...]
  estimated_tasks: 4
  ```
- `$ARTIFACTS_DIR/project-context.md`, `$ARTIFACTS_DIR/spinal-files.yaml`.
- Output:
  - `$ARTIFACTS_DIR/feature-plan-manifest.yaml`
  - `$ARTIFACTS_DIR/tareas/F<NN>-<slug>.md` (1 archivo por task)

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO, PRIMERO)

1. `$ARTIFACTS_DIR/project-context.md`.
2. `$ARTIFACTS_DIR/plugin-work.yaml` (tu slice).
3. `$ARTIFACTS_DIR/hu-refinada.md` (full refinement — buscás §3.X de tu plugin).
4. `$ARTIFACTS_DIR/plugin-manifest.yaml` (para entender deps cross-plugin).
5. `$ARTIFACTS_DIR/spinal-files.yaml`.
6. Del guide arquitectural (`.claude/skills/hubara-architecture-guide/`):
   - `SKILL.md` + `sections/01-general.md` (siempre).
   - Según `template` de tu plugin (en plugin-work.yaml):
     - **A** (frontend-only): `sections/06-frontend-plugin.md` + `examples/plugin-frontend-only.md`
     - **B** (frontend + API): `sections/06-frontend-plugin.md` + `examples/plugin-frontend-plus-api.md` + `sections/03-backend-plugin.md`
     - **C** (frontend + worker): `sections/03-backend-plugin.md` + `sections/04-backend-agents.md` + `examples/plugin-with-worker.md`
     - **D** (full-stack agéntico): `sections/03-backend-plugin.md` + `sections/04-backend-agents.md` + `sections/06-frontend-plugin.md` + `examples/plugin-full-stack-agentic.md`
   - Si `affects_shared_files` no vacío: `sections/07-shared-files.md`.
   - Siempre: `sections/08-tests-and-gates.md` + `sections/10-cookbook.md`.

---

## §1.5 Exploración plugin-level delegada (OBLIGATORIO en iteración 1)

> **Eleva la Técnica 15 del HARNESS_ENGINEERING.md.** Antes de descomponer en tasks,
> mapeá el plugin con un subagent read-only para que las tasks respeten las fronteras
> reales del código existente (no inventes módulos paralelos cuando ya existen helpers).

### §1.5.1 ¿Cuándo aplica?

**Iteración 1 (primera vez planificando este plugin):** OBLIGATORIO si `plugin_work.action` es `extend` o `refactor` (plugin existente).

**Iteración 1 con `action: create`:** OMITIR (no hay código previo que explorar; el template del guide §3.4 es suficiente).

**Iteración >1:** OMITIR (ya hay un feature-plan-manifest.yaml previo + tareas. Si el explorer hubiera detectado algo crítico, ya está reflejado en la versión previa).

### §1.5.2 Cómo invocar el explorer (plugin-scope)

1. **Read** `.claude/skills/hubara-explorer-archon/SKILL.md` — template de prompt.
2. Sustituir placeholders con scope plugin-level:
   - `<TASK_ID>` ← `plugin-exploration-<plugin_id>` (informativo)
   - `<PATHS_TO_TOUCH>` ← lista de paths de TODOS los archivos que la HU tocará en ESTE plugin (extraído de `hu-refinada.md` §3 + `plugin-work.yaml`)
   - `<AFFECTED_LAYERS>` ← unión de capas en `plugin_work.affects_layers`
   - `<PLUGIN_ID>` ← `plugin_work.plugin_id`
   - `<HU_ID>` ← del refinement
3. **Importante:** agregá al prompt rendered un párrafo extra:

   ```
   Scope adicional para feature-planning: además del protocolo §3 del template,
   listá los módulos top-level del plugin <PLUGIN_ID> (uno por dir/archivo dominante)
   y para cada uno indicá su responsabilidad en ≤1 línea. Esto se usa para definir
   "feature boundaries" de las tasks F<NN>.
   ```

4. **Invocá** `Agent(subagent_type="Explore", description="Plugin-level map for <plugin_id>", prompt=<rendered>)`.
5. Persistí la salida en `$ARTIFACTS_DIR/plugin-exploration-map.md`.

### §1.5.3 Cómo usar el exploration map en la descomposición

Al construir el DAG de tasks (§3 abajo):

- **Una task = un módulo top-level del plugin** cuando sea posible. Si el explorer dijo que el plugin tiene 3 módulos top-level (`agent/`, `workers/`, `api/`), considerá no mezclar capas en la misma F<NN> a menos que la HU literalmente requiera coupling cross-módulo.
- **Sibling patterns:** cada task F<NN> debería referenciar el sibling canónico en su §5 Snippets para que el implementer no re-explore.
- **Tests afectados:** alimentá la sección §9 Tests de cada task con los test files que el explorer listó (en lugar de inventar paths).
- **Workspace deltas:** si el explorer flageó workspace/*.md que requiere update, asigná esa porción a la task que también modifica el código asociado (no separes en F-task aparte).

### §1.5.4 Manejo de flags

| Flag del explorer | Acción del feature-planner |
|---|---|
| `exploration_capped: true` | El plugin es demasiado grande para un solo plugin-exploration. Aceptable — el explorer dio lo que pudo. Anotá `exploration_partial: true` en feature-plan-manifest.yaml metadata. |
| `codegraph_stale: true` | Anotá en cada task F<NN> afectada: "verificar símbolo X con Read antes de editar" en §1 Context. |
| `mode: blocked` | El plugin pide modificar protected paths. Propagá `status: blocked, blocked_reason: requires_architecture_change` al pipeline. NO emitir feature-plan-manifest.yaml. |

### §1.5.5 ¿Y si Agent tool no está disponible?

Mismo patrón que el §1.5.5 del implementer: caer a exploración inline con codegraph_* + Read, con budget de tool calls. Anotá `exploration_mode: inline_fallback` en metadata del manifest.

---

## §2. Iteration handling

En cada invocación:

1. Re-leé `plugin-work.yaml` (siempre).
2. Si `feature-plan-manifest.yaml` ya existe → iteración >1:
   - Leé versión previa + cada `tareas/F<NN>-*.md` existente.
   - Leé `$LOOP_USER_INPUT`.
   - Aplicá feedback puntual (split, merge, renumerar deps).
3. Incrementá `iteration`.

---

## §3. Algoritmo del feature-level DAG

### §3.1 ¿Qué es una "atomic feature task"?

Vertical slice — la unidad más chica que:

- Entrega ≥1 acceptance criterion de la HU (o coherent subset).
- Cruza TODAS las capas FSD/DEHA que toca (no "F01: solo DTO" / "F02: solo tool que usa DTO" — bundleá).
- Es testeable solo (después de sus `depends_on`, suite verde).
- Size band: 50-300 LOC neto (excluyendo tests).
  - <50: bundle con parent.
  - >300: considerá split.

### §3.2 Heurística por layer

| Layer del plugin tocada | Pattern |
|---|---|
| `agent` (backend) | 1 feature = 1 tool LLM + sus DTOs + composition + worker registration + workspace TOOLS.md + tests |
| `agent` (workflow nuevo) | 1 feature = workflow + sus activities + composition + worker registration + tests |
| `api` | 1 feature = endpoint + sus DTOs + tests + manifest update si aplica |
| `frontend` | 1 feature = component / hook / page mount + sus contracts + tests vitest + e2e spec |

### §3.3 Cuándo bundle vs split

| Caso | Decisión |
|---|---|
| Tool + sus DTOs cross-only-tool | bundle |
| Tool + workflow nuevo que SOLO el tool dispara | bundle |
| Entity nuevo + feature único consumer | bundle |
| Entity nuevo + 2+ features consumer | split (entity = foundation task con depends_on=[]) |
| Tailwind tokens feature-specific | bundle con feature |
| Tailwind tokens cross-feature design system | split (foundation task) |
| Backend dependency (endpoint nuevo que el feature consume) | flag en §13 — el operador resuelve antes |

### §3.4 DAG validation

- No cycles.
- Max 3 `depends_on` directos por task.
- ≥1 task con `depends_on: []` (foundation).
- Chain lineal >7 = red flag.
- Cada task `delivers_acceptance` ≥1 AC de la HU.
- Suma de `delivers_acceptance` cubre TODOS los AC.

#### Task count cap (HARD)

- **Default `MAX_FEATURES_PER_PLUGIN = 12`** (override con env var del mismo nombre).
- Si `len(tasks) > MAX_FEATURES_PER_PLUGIN` → emitir feature-plan-manifest blocked:
  ```yaml
  mode: blocked
  blocked_reason: too_many_features
  blocked_detail: "Plugin requiere N tasks > cap=12. Splittear el plugin work en 2 HUs."
  tasks_proposed: [<lista de task titles detectados>]
  ```
- Racional: el loop de implementación es secuencial dentro del plugin
  (default 1 task por batch). >12 tasks = pipeline corre >2hs sin checkpoint
  natural; si falla en task 11, perdés todo el progreso. Mejor splittear.
- El cap es HARD — para tasks legítimamente extensas, dividir en 2 HUs
  ortogonales (e.g. "HU-A: backend de la feature" + "HU-B: frontend + e2e").

#### Foundation count cap (SOFT warning)

- Si `foundation_count > 4` (tasks con `depends_on: []`) → warning en
  notes: `"4+ foundations sugiere falta de estructura — considerá bundlear"`.

### §3.5 Parallel batches dentro del plugin

A diferencia del plugin-planner, **dentro del plugin las tasks suelen ser
secuenciales** porque comparten worker.py, composition.py, manifest, etc.

Default: cada task en su propio batch.

Excepciones (las tasks pueden compartir batch):
- Una task toca solo agent/sales/tools/, otra solo agent/sales/activities/ — sin overlap.
- Una task toca solo frontend, otra solo backend.

Si dudás, ponelas en batches separados (más seguro, no degrada UX porque
todo el plugin corre secuencial igual).

---

## §4. Output template — `feature-plan-manifest.yaml`

```yaml
version: 1
hu_id: <id>
plugin_id: chats
plugin_template: D
generated_by: hubara-feature-planner-archon
generated_at: <ISO 8601>
iteration: <n>

totals:
  task_count: 4
  foundation_count: 1                    # tasks con depends_on=[]
  dag_max_depth: 2
  estimated_loc_total: 320
  acceptance_coverage: ['AC-1', 'AC-2']

tasks:
  - id: F01
    title: "Crear DTO ImageMessage + tool SendImage"
    slug: image-message-dto-tool
    file: tareas/F01-image-message-dto-tool.md
    depends_on: []
    blocks: ['F02', 'F03']
    delivers_acceptance: ['AC-1']
    affects_layers: ['contracts', 'tools', 'composition', 'worker', 'tests']
    affects_new_files:
      - hubara_agency/src/plugins/chats/agent/sales/contracts.py
      - hubara_agency/src/plugins/chats/agent/sales/tools/send_image.py
      - hubara_agency/tests/plugins/chats/tools/test_send_image.py
    affects_spinal_files:
      - hubara_agency/src/plugins/chats/workers/sales.py
      - hubara_agency/src/plugins/chats/agent/sales/composition.py
      - hubara_agency/src/plugins/chats/agent/sales/workspace/TOOLS.md
    estimated_loc: 100
    risk: low
  # ... F02, F03, F04

parallel_batches:
  - batch_id: B1
    tasks: ['F01']
    warnings: []
  - batch_id: B2
    tasks: ['F02']                       # depende de F01
    warnings: []
  # ...

notes: |
  ...
```

---

## §5. Output template — `tareas/F<NN>-<slug>.md`

Cada task file con esta estructura EXACTA (espejo de los planners
existentes con ajustes hubara):

```markdown
# Task F<NN> — <Feature title>

- Slug: <hyphen-slug>
- HU id: <id>
- Plugin id: <plugin_id>
- Plugin template: A | B | C | D
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections cited inline)
- Planner: hubara-feature-planner-archon
- Date: <YYYY-MM-DD>
- Iteration: <n>
- Estimated LOC: <int>
- Risk: low | medium | high

## §1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- AC-<id>: <text>

Refinement sections que informaron esta task: §3.X, §3.Y.

Code anchors del refinement (relevantes a esta task):
- Pattern: <patrón> at <path:line> — <rationale>
- File to extend: <path>
- File to create: <path>

Assumptions del refinement §15 que afectan esta task:
- A<n>: <assumption> | default: <chosen> | reversibility: <low|medium|high>

## §2. Dependencies

- depends_on: [<F-ids>]
- blocks: [<F-ids>]
- Inherits from upstream: <one-liner — qué dejó la task anterior listo>
- Cross-plugin dependency: <none | plugin <id> debe haber mergeado a hu/<HU_ID>>
- Backend dependency: <endpoint que debe existir, o "none">

## §3. Files affected

| Path | Acción | Rol | LOC budget |
|---|---|---|---|
| hubara_agency/src/plugins/<id>/agent/<sub>/contracts.py | modify | DTO | +12 |
| hubara_agency/src/plugins/<id>/agent/<sub>/tools/<concept>.py | new | Tool | ~80 |
| hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md | modify | workspace | +4 |
| hubara_agency/src/plugins/<id>/workers/<worker>.py | modify | worker registration | +2 |
| hubara_agency/src/plugins/<id>/agent/<sub>/composition.py | modify | factory | +6 |
| hubara_agency/tests/plugins/<id>/tools/test_<tool>.py | new | tests | ~60 |
| hubara_agency/tests/functional/test_<tool>_e2e.py | new | functional test | ~50 |

## §4. Boundary DTOs (R-JSON) — solo si task toca DTOs

```python
# canonical — src/plugins/<id>/agent/<sub>/contracts.py
@dataclass(frozen=True)
class <NewDto>:
    field_a: str
    field_b: int
```

## §5. Snippets canónicos (≤15 líneas c/u, marked # canonical)

```python
# canonical — src/plugins/<id>/agent/<sub>/tools/<concept>.py
from exoclaw.agent.tools import ToolBase, ToolContext
class <NewTool>(ToolBase):
    name = "<llm_name>"
    description = "<from refinement §3.X>"
    parameters = {...}
    async def execute_with_context(self, ctx, **kwargs) -> str:
        ...
```

(Snippets adicionales según files en §3 — activity, workflow, frontend
component, etc. Cada uno ≤15 líneas marcado `# canonical` / `// canonical`.)

## §6. Workspace deltas (si task toca workspace/*.md)

`workspace/TOOLS.md` delta:

```
+ ## <tool name>
+ Cuándo llamar: <one-liner>
+ Cuándo NO llamar: <one-liner>
+ Returns: JSON `{"status": "ok", ...}`
```

## §7. Composition wiring (si task toca composition.py)

```python
# canonical — src/plugins/<id>/agent/<sub>/composition.py
@lru_cache(maxsize=1)
def get_<thing>(workspace_path: str) -> <Type>:
    return <Type>(workspace_path=workspace_path)
```

## §8. Worker registration (si task toca worker.py)

Líneas exactas a agregar a `src/plugins/<id>/workers/<worker>.py`:

```python
from src.plugins.<id>.agent.<sub>.tools.<concept> import <NewTool>
register_tool_extension(
    "<id>.<tool_name>",
    lambda workspace_path: <NewTool>(workspace_path=str(workspace_path)),
)
```

(Si activities=[...]: agregar entry. Si workflows=[...]: agregar entry.)

## §9. Tests

| Test file | New/modify | Scenarios |
|---|---|---|
| hubara_agency/tests/plugins/<id>/tools/test_<tool>.py | new | protocol compliance, happy path con tmp_path, error envelope |
| hubara_agency/tests/functional/test_<feature>_e2e.py | new | end-to-end (user → LLM tool → reply) con mock_llm |
| frontend_dashboard/e2e/<feature>/<slice>.spec.ts | new | playwright user-observable outcome |

Test name list (el implementer escribe los bodies):

- `tests/plugins/<id>/tools/test_<tool>.py::test_<tool>_returns_ok_envelope`
- `tests/plugins/<id>/tools/test_<tool>.py::test_<tool>_raises_when_missing_session`
- `tests/functional/test_<feature>_e2e.py::test_<outcome>`

## §10. Verification commands

Exact commands el implementer va a correr desde REPO ROOT.

```bash
# Unit tests del tool
cd hubara_agency && uv run pytest tests/plugins/<id>/tools/test_<tool>.py -xvs

# Functional test
cd hubara_agency && uv run pytest tests/functional/test_<feature>_e2e.py -m functional -v

# Lint + type
cd hubara_agency && uv run ruff check src/plugins/<id>/
cd hubara_agency && uv run mypy src/plugins/<id>/

# Architecture gate
cd hubara_agency && uv run pytest -m architecture --tb=short
cd hubara_agency && uv run lint-imports

# Si tocó manifest:
cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml

# Si tocó frontend:
cd frontend_dashboard && npm test -- <id>/<feature>
cd frontend_dashboard && npm run test:arch
cd frontend_dashboard && npx tsc -b
cd frontend_dashboard && npm run build
cd frontend_dashboard && npx playwright test e2e/<feature>/
```

## §11. Definition of Done

- [ ] Todos los files en §3 created/modified.
- [ ] Snippets §4-§8 instanciados con implementations completas (no stubs).
- [ ] Todos los §10 commands exit 0.
- [ ] No regresión en suite completa (`uv run pytest tests/` + `npm test`).
- [ ] Architecture gate (pytest -m architecture) exit 0.
- [ ] No edits a `tests/architecture/`, `.importlinter`, `R_*_EXEMPTIONS`,
      `.dependency-cruiser.cjs`, `.archon/workflows/`, `.claude/skills/hubara-*`.
- [ ] Workspace deltas §6 presentes en disk (si aplica).
- [ ] Worker registration §8 presente en disk (si aplica).
- [ ] R-rules check §12 verificadas.
- [ ] Functional test §9 con assertion meaningful + verbose output.
- [ ] Playwright spec §9 con `getByRole` / `getByText` (sin `waitForTimeout`).

## §12. Hard rules check (R-rules + FSD + manifest)

Por cada regla, declarar si aplica + cómo se cumple:

- **R-DET:** <applies / N/A> — <cómo>
- **R-JSON:** <applies / N/A> — <cómo>
- **R-STATELESS:** <applies / N/A> — <cómo>
- **R-HEARTBEAT:** <applies / N/A> — <cómo>
- **R-DIP:** <applies / N/A> — <cómo>
- **R-DIP #10 cross-worker (ADR-2026-05-20):** <applies / N/A> — si la HU
  hace que un workflow arranque/signale otro workflow registrado por OTRO
  worker, **debe usar declarative orchestration**:
  - shared/contracts/events.py con `@dataclass(frozen=True)`
  - manifest.workers[].emits + transitions
  - workflow → `dispatch_event_activity` + `envelope_for(...)`
  - NUNCA import directo de workflow class de sibling
  - flag la task con `cross_worker_dispatch: true` en metadata para que
    el implementer sea explícito sobre el patrón
- **Orchestration footguns (ADR-2026-05-20 premortem):** <applies / N/A> —
  si la HU toca:
  - un **Input dataclass** que es target de un transition (e.g.
    `SalesSessionInput`, `RemarketingSessionInput`), o
  - un **plugin.yaml** con `transitions[]` (cambio en `input_mapping` o
    `target_workflow`), o
  - un **workflow** existente refactorizado con `workflow.patched()`, o
  - el dispatcher genérico (`src/platform/orchestration/`),

  marcá la task con `orchestration_contract_change: true` en metadata + agregá
  a `delivers_acceptance` lo siguiente:

  - ☐ Si agregás campo NUEVO al Input dataclass: tiene `default` value O hay
    `input_mapping` en todas las transitions que apuntan al workflow target
    O el bootstrap activity hace fallback (uno de los 3, explicitar cuál).
  - ☐ `workflow.patched(<descriptive-v1>)` gates si refactorizás existente +
    ambas ramas con paridad de activity counts (o helper method encapsulado).
  - ☐ Tests: `test_dict_to_dataclass_contract.py` (marked functional) +
    `test_manifest_orchestration_consistency.py` (architecture) PASS.
  - ☐ Bootstrap activity nuevo (si aplica) tolera `runtime_workspace_path=None`
    con fallback local (ver §5.6 deha-rules).

  Si no marcás esto, el implementer caerá en uno de los 4 footguns del
  premortem (ver `references/deha-rules.md §5.7`).
- **FSD layering:** <applies / N/A> — <cómo>
- **Manifest = SSoT:** <applies / N/A> — <cómo>

## §13. Open questions / risks

- <Open question carry-over del refinement §13>. Recommended default: <...>.
- <Risk específico a esta task (e.g. "DTO con 8 fields — verificar antes")>.
- Iteration <n> changed: <qué cambió vs iter anterior + por qué>.

## §14. Wiring intents (solo si task toca spinal files)

(Si §3 tiene archivos en `affects_spinal_files`, listar wiring_intents
que el implementer debe declarar en task-result.yaml. Vocabulario
completo en `.claude/skills/hubara-architecture-guide/sections/07-shared-files.md §3`.)

```yaml
# Ejemplo — tool nueva tocando worker.py + composition.py + TOOLS.md
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
        Cuándo NO llamar: ...
        Returns: ...
```
```

---

## §6. Architecture-protected check (HARD STOP)

Si el work_summary o cualquier campo del plugin-work pide modificar
archivos `protected: true` del spinal-files.yaml → emitir
feature-plan-manifest.yaml con:

```yaml
mode: blocked
blocked_reason: requires_architecture_change
notes: |
  El plugin-work pide modificar archivo architecture-protected
  (<lista paths>). Operador: ADR + PR separado architecture-change.
```

Sin tareas.

---

## §7. Style rules

- **Be terse**: tablas > paragrafos.
- **Snippets son shape**: ≤15 líneas marcado `# canonical`. NO full code.
- **NO tests bodies**: solo nombres + scenarios one-liners.
- **NO split por layer**: una task owns TODAS las layers que toca.
- **NO bundle infra cruzada**: si la task necesita heartbeat decorator nuevo,
  es su propio task foundation (raro).
- **Cite refinement subsection** cada decision (e.g. "from refinement §3.1.4").
- **Self-contain**: la task debe ser suficiente para el implementer sin
  re-leer hu-refinada.md.

---

## §8. Salida final

Escribir `$ARTIFACTS_DIR/feature-plan-manifest.yaml` + cada task file.

Print summary 6-líneas:

```
feature-plan emitido para plugin <id>
task_count: <N>
foundation_count: <M>
dag_max_depth: <D>
estimated_loc_total: <L>
iteration: <n>
```

NO imprimir "next steps".

---

**Fin SKILL.**
