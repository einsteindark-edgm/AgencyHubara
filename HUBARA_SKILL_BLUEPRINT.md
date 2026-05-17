# Blueprint — Skill `hubara-architecture-guide` + 5 skills delgados del pipeline

> **Status:** especificación detallada (2026-05-16). Sin ejecutar.
> **Companion docs:**
> - `HUBARA_PIPELINE_PLAN.md` — plan maestro + decisiones.
> - `HUBARA_WORKFLOWS_BLUEPRINT.md` — pseudocódigo de los 3 workflows.

Este documento es el ÍNDICE de contenido (no el contenido completo) para
los 6 SKILL.md + secciones / referencias / ejemplos que entrega el PR12 +
parte de PR14-16.

---

## §1. Convenciones de escritura para todos los skills

- **Frontmatter Archon:** `name`, `description`, `model` (sonnet default,
  haiku para classifiers). El `description` arranca con una sentence-fragment
  que dice qué hace el skill y termina con `Triggers - ...` indicando si
  se invoca directo o solo desde workflow.
- **Idioma:** español rioplatense (igual que el resto del repo y los skills
  existentes). Comentarios técnicos pueden ser inglés cuando son citas de
  Temporal/FastAPI/etc.
- **Tono:** denso, terso, opinionated. No "puedes considerar"; "usá X".
  No "Note that"; el sujeto está claro.
- **Snippets:** ≤15 líneas marcados `# canonical` o `// canonical`. NO
  full implementations en el skill.
- **Tablas > paragrafos** cuando hay 3+ items con mismas columnas.
- **Cita siempre** archivo:línea cuando referenciás algo del repo
  (e.g. `src/platform/plugin_manifest.py:42`).

---

## §2. `.claude/skills/hubara-architecture-guide/SKILL.md`

### §2.1 Estructura

```yaml
---
name: hubara-architecture-guide
description: |
  Single-source-of-truth de la arquitectura de AgencyHubara (DEHA backend +
  FSD frontend + plugin system post-PR11). Diseñado para ser invocado por
  skills del pipeline Archon (hubara-tech-refiner-archon,
  hubara-plugin-planner-archon, hubara-feature-planner-archon,
  hubara-implementer-archon, hubara-merger-archon). Cada skill especialista
  pide al implementer "leé sections/0N-<area>.md" en vez de duplicar
  contenido en su propio prompt. NO escribe código; solo retorna
  conocimiento. Triggers - invocación directa via Read tool; no usar como
  subagent ni invocar via Skill tool.
---
```

### §2.2 Cuerpo (resumen — el archivo real escribe esto desplegado)

| Sección del SKILL.md | Qué incluye |
|---|---|
| Cómo se usa | Convención: skills downstream leen este SKILL.md primero, luego cargan sólo las `sections/0N.md` que matchean su tarea |
| Tabla de secciones | 22 entries del index del PLAN §3.2 (10 sections + 4 refs + 4 examples + SKILL.md + README.md + 2 implícitos: navegación + glosario) |
| Mapa rápido del repo | Diagrama ASCII condensado del layout: `hubara_agency/`, `frontend_dashboard/`, `.archon/` |
| Reglas DURAS (no negociables) | Lista de 7 reglas (5 R-rules + FSD layering + plugin manifest=SSoT) con enforcement |
| Anti-patterns top-5 | Los 5 errores más comunes que los implementers cometen + cómo evitarlos |

### §2.3 README.md (acompañante)

Un archivo separado `README.md` (no es parte del SKILL.md, no lo carga
Claude por default) explicando para devs humanos:

- Por qué este skill existe (history: refactor PR11, demanda de pipeline paralelo).
- Cómo se mantiene (cuándo agregar / editar secciones).
- Convención: secciones >15 KB se splittean en sub-archivos `0N-tema-<subseccion>.md`.
- Cómo testear cambios al skill (ejecutar el pipeline con un HU dummy).

---

## §3. Contenido de cada sección

### §3.1 `sections/01-general.md` (~12 KB)

| Bloque | Origen del material | Líneas estimadas |
|---|---|---|
| Vista 30k pies | `ARCHITECTURE.md §1` condensado | 30 |
| Stack tecnológico (tabla) | `ARCHITECTURE.md §1` | 20 |
| Layout completo del repo (ASCII) | `ARCHITECTURE.md §2` | 60 |
| Sistema de plugins — qué es | `ARCHITECTURE.md §3.1` | 20 |
| Anatomía del manifest | `ARCHITECTURE.md §3.2` | 80 |
| Los 3 loaders | `ARCHITECTURE.md §3.3` (incluye el ASCII art) | 40 |
| Quién lee qué del manifest (tabla) | `ARCHITECTURE.md §3.4` | 30 |
| El frontend registry generado | `ARCHITECTURE.md §3.5` | 25 |
| Diagrama de componentes (mermaid) | `ARCHITECTURE.md §4` | 80 |
| Aislamiento por task queue (tabla) | `ARCHITECTURE.md §4` | 20 |
| Paralelismo de implementadores | `ARCHITECTURE.md §14` condensado | 40 |
| **TOTAL** | | ~445 líneas / ~12 KB |

### §3.2 `sections/02-backend-platform.md` (~10 KB)

| Bloque | Origen / Notas | Líneas |
|---|---|---|
| Mapa de `src/platform/` (qué hay en cada subdir) | inspeccionar listado de §3.0 del PLAN | 40 |
| `plugin_manifest.py` — API completa | leer el archivo + docstring | 50 |
| `contracts.py` — cross-plugin DTOs + R_JSON_FROZEN_EXEMPTIONS | leer archivo | 30 |
| `registries.py` — base registry pattern | leer archivo | 20 |
| `tool_extensions.py` — DI invertida | leer archivo + ejemplo de uso | 35 |
| `workflow_helpers.py` — `run_agent_turn` + `coalesce_pending` | leer archivo (12 KB en el repo) | 60 |
| `temporal/` — client, dispatcher, heartbeat, retry_policies | listing + 1 ejemplo de cada | 50 |
| `whatsapp/` — client + activities | listing | 25 |
| `session_history/` — JSONL store | listing | 25 |
| `catalog/` — CatalogPort + LocalSnapshot + MedusaCheckoutVerification | listing | 30 |
| `medusa/` — HTTP client live | listing | 20 |
| `tools/` — shared LLM tools | listing | 20 |
| Cuándo escribir EN platform vs EN un plugin (regla de oro) | nueva | 25 |
| **TOTAL** | | ~430 líneas / ~10 KB |

### §3.3 `sections/03-backend-plugin.md` (~12 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Templates A-D | `ARCHITECTURE.md §7.1-§7.5` condensado | 200 |
| Checklist post-PR11 | `ARCHITECTURE.md §7.5` | 30 |
| Lo que NO hay que hacer | `ARCHITECTURE.md §7.5` final | 20 |
| Verificación local (smoke imports + discovery + tests) | `ARCHITECTURE.md §7.3` final | 30 |
| Cómo registrar tools desde el worker | `ARCHITECTURE.md §7.3` + `tool_extensions.py` ejemplo | 30 |
| Cómo declarar wiring_intents en manifest | `ARCHITECTURE.md §3.2` snippet | 25 |
| Cómo agregar K8s manifest del worker | `ARCHITECTURE.md §7.3` "Crear K8s deployment" + ejemplo real | 50 |
| **TOTAL** | | ~385 líneas / ~12 KB |

### §3.4 `sections/04-backend-agents.md` (~14 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Anatomía de un agent Temporal | `chats/agent/sales/` listing + roles | 30 |
| Workflows: `run_agent_turn` (tool loop) | `workflow_helpers.py:run_agent_turn` + comentarios | 60 |
| Workflows: debounce 1.5s silence / 12s cap | `ARCHITECTURE.md §5.2` | 25 |
| Workflows: continue-as-new cada 50 turnos | `ARCHITECTURE.md §5.7` | 20 |
| Workflows: `workflow.patched()` para features gated | `ARCHITECTURE.md §5.4` | 25 |
| Activities: heartbeat + retry policies | `temporal/heartbeat.py` + `retry_policies.py` ejemplos | 50 |
| Activities: stateless (no module-level cache) | nueva con ejemplo de violación | 25 |
| Tools: `ToolBase` + `ToolContext` + `execute_with_context` | snippet canónico | 40 |
| Tools: decisión vs acción (ADR-001) | `ARCHITECTURE.md §5.5` | 30 |
| Composition factories: `@lru_cache(maxsize=1)` | `composition.py` ejemplos | 25 |
| Worker registration: `register_tool_extension` + `workflows=[]` + `activities=[]` | `workers/sales.py` snippet | 35 |
| Workspace deltas: `TOOLS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `AGENTS.md` | descripción de cada + cuándo editar | 40 |
| Prompts.py: constants vs templates | snippet | 25 |
| Task queue resolution (usá `get_task_queue`, no `constants.py`) | post-PR11 | 25 |
| **TOTAL** | | ~455 líneas / ~14 KB |

### §3.5 `sections/05-frontend-fsd.md` (~10 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Capas FSD (5 capas) | `ARCHITECTURE.md §8.2` | 40 |
| Reglas extra del refactor (dep-cruiser) | `ARCHITECTURE.md §8.2` final | 30 |
| Plugin registry generado (lifecycle) | `ARCHITECTURE.md §3.5` + `plugins-sync.ts` mecanismo | 50 |
| `Dashboard.tsx` 100% data-driven | `Dashboard.tsx` snippet | 30 |
| `Toolbar.tsx` sections dinámicas + Icon registry fallback | `Toolbar.tsx` snippet | 35 |
| `entities/` shared cross-plugin | tabla con las 8 entidades actuales | 30 |
| `features/` legacy (relaxation: cross-feature dentro del plugin OK) | nota | 20 |
| `shared/ui/` primitives + Icon | listing | 30 |
| `app/providers/` chain | listing | 25 |
| Zod at boundary (rule) | snippet | 20 |
| TanStack Query for server data (rule) | snippet | 20 |
| Tailwind v4 tokens en `@theme` | snippet | 30 |
| Vite + Tauri config (build modes) | overview | 25 |
| **TOTAL** | | ~385 líneas / ~10 KB |

### §3.6 `sections/06-frontend-plugin.md` (~8 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Estructura del frontend de un plugin (4 archivos típicos) | `ARCHITECTURE.md §7.1` + `chats/frontend/` listing | 40 |
| `frontend/index.ts` (barrel) | snippet canónico | 20 |
| `<Id>Section.tsx` (root component + props bandejón) | snippet canónico + descripción del contrato | 40 |
| `frontend/features/` interno (cross-feature OK dentro del plugin) | nota | 20 |
| Sections vs sidebar en el manifest | tabla | 25 |
| `dashboard_widgets` (deferred) | nota | 15 |
| Code splitting via `lazy()` + Suspense | snippet | 25 |
| Icon registry: cómo agregar icon nuevo (hasta que sea plugin-local) | snippet + reminder spinal | 30 |
| Tests del plugin frontend (vitest + RTL) | snippet | 30 |
| Plugin con badge dinámico (`badge_query`) | reservado, mencionar | 15 |
| **TOTAL** | | ~260 líneas / ~8 KB |

### §3.7 `sections/07-shared-files.md` (~10 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Definición de spinal file (qué es, por qué importa) | nueva | 20 |
| Tabla COMPLETA de shared files (post-PR11) | `HUBARA_PIPELINE_PLAN.md §2` | 50 |
| Conflicts mecánicos (compose, locks, log) y cómo resolverlos | `ARCHITECTURE.md §14.2` + comandos | 30 |
| Cuándo invocar `hubara-merger-archon` (criterio) | nueva | 25 |
| Vocabulario de wiring_intents (todos los kinds posibles) | adaptado de `exoclaw-implementer-archon` y `frontend-implementer-archon` | 80 |
| Reglas para wiring_intents (formato, sorting, idempotencia) | adaptado | 40 |
| Lo que NUNCA es spinal (single-owner) | nueva tabla | 20 |
| Tests que enforzan el isolation | `ARCHITECTURE.md §14.4` | 30 |
| **TOTAL** | | ~295 líneas / ~10 KB |

### §3.8 `sections/08-tests-and-gates.md` (~10 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Architecture gate Python: `uv run pytest -m architecture` | listing tests | 30 |
| `tests/architecture/` qué archivos protege | nueva | 25 |
| `R_JSON_FROZEN_EXEMPTIONS` y `R_HEARTBEAT_EXEMPTIONS` (cuándo agregar — casi nunca) | nueva | 25 |
| `.importlinter` contratos R-DIP | `ARCHITECTURE.md §8.1` + listado contratos | 30 |
| Premortem invariants (`tests/plugins/test_premortem_invariants.py`) | listado de los 6 tests + qué bloquea cada uno | 40 |
| Architecture gate Frontend: `npm run test:arch` | listing tests | 30 |
| `dependency-cruiser` rules | `ARCHITECTURE.md §8.2` final | 30 |
| Render-compose check (nuevo post-PR11) | `tests/plugins/test_premortem_invariants.py:test_docker_compose_*` | 25 |
| Architecture protected files (HARD STOP) | de `exoclaw-implementer-archon` adaptado a hubara | 35 |
| META-GATE failures (nunca passed) | de `exoclaw-implementer-archon` adaptado | 30 |
| Functional tests pattern (`tests/functional/`) | de `exoclaw-implementer-archon` | 30 |
| Playwright E2E pattern (`e2e/`) | de `frontend-implementer-archon` | 35 |
| Cómo correr toda la suite localmente | comandos | 25 |
| **TOTAL** | | ~390 líneas / ~10 KB |

### §3.9 `sections/09-conventions.md` (~8 KB)

| Bloque | Origen | Líneas |
|---|---|---|
| Naming: plugin id, task queue, worker name, K8s deployment | `ARCHITECTURE.md §3.2` + `_schema/plugin.schema.yaml` patterns | 25 |
| HU id format: `HU-<YYYYMMDD>-<HHMMSS>-<slug>` | nueva (heredado de frontend pipeline) | 15 |
| Branch strategy: `hu/<HU_ID>` | nueva | 15 |
| Secrets K8s: `env_secrets` en manifest | `chats/plugin.yaml` ejemplo | 30 |
| Env vars: `wiring_intents.env_vars_required` | ejemplo | 20 |
| `render-compose.py`: cuándo correr + qué genera | `ARCHITECTURE.md §11` final | 25 |
| `plugins-sync.ts`: cuándo correr + qué genera | `ARCHITECTURE.md §3.5` | 20 |
| Vault paths: `wa_*/`, `catalog/` (sub-namespaces) | `ARCHITECTURE.md §6` | 30 |
| Vault hygiene en tests (`_isolate_vault_dir`) | `ARCHITECTURE.md §6.2` | 25 |
| Logging: `setup_logging()` + loguru | `src/platform/logging.py` | 20 |
| Convenciones de PR title / commit message | nueva | 20 |
| **TOTAL** | | ~245 líneas / ~8 KB |

### §3.10 `sections/10-cookbook.md` (~15 KB)

12-15 recetas con título imperativo + pasos + snippet canónico + comando
de verificación. Lista propuesta:

1. **Agregar tool LLM nuevo a un agente Temporal** (template C/D)
2. **Agregar activity nueva (no LLM) a un agente** (template C/D)
3. **Agregar workflow nuevo a un plugin** (template C/D)
4. **Agregar webhook endpoint nuevo a un plugin con API** (template B/D)
5. **Agregar SSE endpoint nuevo al dashboard** (template B/D)
6. **Agregar feature frontend nuevo dentro de un plugin existente** (cualquier template)
7. **Agregar entity shared cross-plugin** (toca `src/entities/`)
8. **Agregar shared/ui/ primitive** (toca `src/shared/ui/`)
9. **Crear plugin nuevo template A (frontend-only)** — el más simple
10. **Crear plugin nuevo template D (full-stack agéntico)** — el más completo
11. **Promover plugin de A a D (sumarle worker)** — añadir worker a un plugin existente
12. **Agregar nueva queue Temporal a un plugin** — vía manifest (post-PR11)
13. **Manejar fallo de architecture gate** — diagnose + fix path
14. **Manejar fallo de render-compose check** — qué pasó + cómo corregir
15. **Bloquearse correctamente (`status: blocked`)** — cuándo usar `requires_planner_update` vs `regression` vs `command_timeout`

Cada receta: ~25-30 líneas. Total: ~400-450 líneas / ~15 KB.

---

## §4. References (4 archivos)

### §4.1 `references/manifest-schema.md`

Reference completa del `plugin.schema.yaml`. Estructura:

| Sección | Contenido | Líneas |
|---|---|---|
| Top-level fields | `id`, `version`, `display_name`, `description`, `depends_on` | 30 |
| `frontend` block | `entry`, `contributes.sidebar`, `contributes.sections`, `contributes.dashboard_widgets` | 50 |
| `api` block | `python_module`, `prefix`, `tags`, `legacy_routers`, `migrations`, política loader | 50 |
| `agent` block | `python_module`, `worker_module`, `workers[]` (name, module, task_queue, deployment, compose) | 80 |
| `jobs` block | `schedule`, `handler` (deferred) | 20 |
| `wiring_intents` block | `db_tables`, `s3_buckets`, `filesystem_volumes`, `env_vars_required` | 30 |
| `permissions` block | `reads`, `writes` (deferred) | 15 |
| Ejemplos completos | 3 manifest reales: catalog (template C), eta (template A), chats (template D) | 100 |

Total: ~375 líneas / ~12 KB.

### §4.2 `references/deha-rules.md`

| Regla | Contenido | Líneas |
|---|---|---|
| Header + intro | qué es DEHA, por qué las reglas existen | 30 |
| R-DET | def + enforcement + ejemplos válidos / inválidos | 60 |
| R-JSON | def + enforcement (test_r_json.py AST scan) + lista de tipos prohibidos | 70 |
| R-STATELESS | def + enforcement (convention) + ejemplos | 40 |
| R-HEARTBEAT | def + enforcement (convention) + decorator example | 50 |
| R-DIP | def + enforcement (import-linter contratos) + 4 contratos detallados | 80 |
| Cómo agregar excepción (proceso ADR) | nueva | 30 |

Total: ~360 líneas / ~11 KB.

### §4.3 `references/fsd-rules.md`

| Sección | Contenido | Líneas |
|---|---|---|
| Header + intro a FSD | espejo de `~/.claude/skills/frontend-feature-sliced/SKILL.md` resumido | 30 |
| Las 4 import rules | shared → entities → features → pages → app | 60 |
| Los 14 anti-patterns (1-3 líneas cada uno) | tabla con expectation + remedio | 120 |
| Excepciones documentadas | nueva | 20 |
| Tests que enforzan | listing de `npm run test:arch` + `dependency-cruiser` rules | 30 |

Total: ~260 líneas / ~8 KB.

### §4.4 `references/temporal-patterns.md`

| Patrón | Contenido | Líneas |
|---|---|---|
| Signal-driven workflows | snippet `@workflow.signal` + cuándo usar vs query | 40 |
| Debounce con `wait_condition` (replay-safe) | snippet del sales workflow | 50 |
| Coalesce pending messages | `coalesce_pending` ejemplo | 25 |
| Continue-as-new pattern | snippet + cuándo disparar (_CONTINUE_AS_NEW_AFTER_TURNS) | 30 |
| `workflow.patched()` para gate features | snippet typing-indicator example | 35 |
| Tool decisions vs activities (ADR-001) | snippet + diagrama | 40 |
| Activity heartbeat (`with_heartbeat(every=10)`) | snippet | 30 |
| Retry policies presets | tabla de presets actuales | 30 |
| Replay tests (cuándo bumpear version del fixture) | nueva | 25 |

Total: ~305 líneas / ~10 KB.

---

## §5. Examples (4 archivos)

Cada ejemplo: tomar un plugin REAL del repo, mostrar todos sus archivos
relevantes (con snippets), y explicar las decisiones.

### §5.1 `examples/plugin-frontend-only.md`

- Plugin: `orders` (o `eta`, o `agents_admin` — elegir el más documentado).
- Estructura del manifest + el frontend/.
- Cómo se monta en el Dashboard sin tocar ningún spinal file.
- Cómo se agregan features adicionales dentro del plugin.

Líneas: ~150 / ~5 KB.

### §5.2 `examples/plugin-frontend-plus-api.md`

- Plugin: **hipotético `reports`** (no existe aún en el repo; el ejemplo
  es educacional para template B).
- Mostrar cómo se vería: manifest con `api.python_module`, FastAPI router
  pegado al loader, endpoint sample.
- Tests para el endpoint.

Líneas: ~180 / ~5 KB.

### §5.3 `examples/plugin-with-worker.md`

- Plugin: `catalog`.
- Estructura completa: manifest + agent/contracts + agent/workflows +
  agent/activities + workers/sync.py + tests + k8s/worker-catalog-sync.yaml.
- Comentar las decisiones DEHA (workflow stateless, activities con heartbeat).

Líneas: ~250 / ~8 KB.

### §5.4 `examples/plugin-full-stack-agentic.md`

- Plugin: `chats` (el más complejo, 2 workers, 3 routers, 8+ tools).
- Estructura completa: manifest + api/{sales,dashboard,handoff} +
  agent/{sales,remarketing} + workers/{sales,remarketing} + tests.
- Comentar las decisiones de arquitectura:
  - Por qué 2 task queues (aislamiento).
  - Por qué `legacy_routers` (3 prefijos heterogéneos).
  - Cómo se registran tools desde el worker.
  - Continue-as-new cada 50 turnos.

Líneas: ~350 / ~12 KB.

---

## §6. Skills delgados del pipeline (5 archivos SKILL.md)

Cada uno es un SKILL.md autocontenido (~5-8 KB) que principalmente:
1. Define el contrato Archon (qué archivos lee, qué escribe).
2. Instruye al modelo a leer las secciones del guide que correspondan.
3. Define el output template específico (manifest, task-result, etc.).

### §6.1 `hubara-tech-refiner-archon/SKILL.md`

**Rol:** convierte una HU cruda en una refinement técnica (espejo de
`frontend-tech-refiner-archon` + `exoclaw-tech-refiner-archon` pero
plugin-aware).

**Cargo del guide:**
- `sections/01-general.md` (siempre)
- `sections/07-shared-files.md` (siempre, para clasificar el blast radius)
- `sections/02-backend-platform.md` si la HU pinta backend
- `sections/05-frontend-fsd.md` si la HU pinta frontend
- `references/manifest-schema.md` para campos opcionales del manifest

**Output:** `$ARTIFACTS_DIR/hu-refinada.md` con las 14 secciones canónicas
(adaptadas de los refiners existentes) + **§0 Plugin classification**:

```markdown
## §0 Plugin classification

- mode: single_plugin | multi_plugin
- plugins_affected:
  - { id: chats, layers: [agent, api], action: extend }
  - { id: catalog, layers: [agent], action: extend }
- shared_files_touched:
  - frontend_dashboard/src/shared/ui/Icon.tsx: { reason: new icon "X" }
  - hubara_agency/src/platform/contracts.py: { reason: nuevo DTO cross-plugin "Y" }
- requires_merger: false | true
```

Esta sección §0 la consume el `hubara-plugin-planner-archon` para decidir
el DAG.

**Estructura del SKILL.md:**
- Frontmatter (~10 líneas)
- Invocation contract (~30 líneas)
- Step 0: cargar contexto (~20 líneas)
- Step 1: leer HU + clasificar (~50 líneas)
- Step 2: producir refinement (~100 líneas con template embedded)
- Style rules (~30 líneas)
- Total: ~250 líneas / ~7 KB.

### §6.2 `hubara-plugin-planner-archon/SKILL.md`

**Rol:** decompone refinement en DAG plugin-level (`plugin-manifest.yaml`)
con plugin-batches.

**Cargo del guide:**
- `sections/01-general.md`
- `sections/07-shared-files.md` (para detectar conflicts entre plugins)
- `references/manifest-schema.md`

**Output:** `$ARTIFACTS_DIR/plugin-manifest.yaml` + descripción por plugin:

```yaml
version: 1
hu_id: HU-...
hu_title: ...
mode: single_plugin | multi_plugin
generated_by: hubara-plugin-planner-archon
generated_at: 2026-05-16
iteration: 1
totals:
  plugin_count: 3
  has_shared_files: false
  requires_merger: false
plugins:
  - id: chats
    title: "Extender chats con tool de envío de imágenes"
    work_summary: "Nuevo tool, registra al worker sales, update workspace/TOOLS.md"
    layers: [agent]
    template: D
    feature_plan_dir: feature-plans/chats/
    depends_on: []          # plugins de los que depende
    blocks: []
    estimated_tasks: 3
    risk: low
  - id: catalog
    title: "..."
    ...
    depends_on: []
    blocks: []
plugin_batches:
  - batch_id: B1
    plugins: [chats, catalog]
    warnings: []
  - batch_id: B2
    plugins: [reports]                # depende de B1
    warnings: []
shared_files_intents:                  # cuando hay shared files
  - file: src/shared/ui/Icon.tsx
    intents:
      - { kind: ts_object_entries_append, name: "image", definition: '...' }
notes: |
  ...
```

**Estructura del SKILL.md:** ~200 líneas / ~6 KB.

### §6.3 `hubara-feature-planner-archon/SKILL.md`

**Rol:** dentro de un plugin, decompone el "work_summary" en un DAG
feature-level (similar al planner exoclaw o frontend pero más simple
porque el scope ya está limitado al plugin).

**Cargo del guide:**
- `sections/03-backend-plugin.md` o `sections/06-frontend-plugin.md` (según layer)
- `sections/04-backend-agents.md` si toca workflows/activities/tools
- `sections/10-cookbook.md` para patrones

**Output:** `$ARTIFACTS_DIR/feature-plan-manifest.yaml` + `tareas/F<NN>-*.md`
(adaptado de planner exoclaw/frontend pero con campo `plugin_id`).

**Estructura del SKILL.md:** ~220 líneas / ~7 KB.

### §6.4 `hubara-implementer-archon/SKILL.md`

**Rol:** implementa UNA tarea atómica dentro de un plugin.

**Cargo del guide (selectivo según task layers):**
- Si la tarea toca Python backend: `sections/02-backend-platform.md` +
  `sections/03-backend-plugin.md` + `sections/04-backend-agents.md`
- Si la tarea toca TypeScript frontend: `sections/05-frontend-fsd.md` +
  `sections/06-frontend-plugin.md`
- SIEMPRE: `sections/08-tests-and-gates.md`
- Si la tarea modifica spinal: `sections/07-shared-files.md`

**Output:** `$ARTIFACTS_DIR/task-result.yaml` (igual schema que
implementer exoclaw y frontend) + edits en el worktree.

**Diferencias clave con los existentes:**
- Corre los gates de AMBOS stacks cuando aplica (uv pytest + npm test).
- Corre `render-compose-check` cuando la tarea modificó el manifest.
- Corre `uv run lint-imports` (R-DIP).
- Soporta cross-stack tasks (e.g. "agregar SSE endpoint" toca FastAPI + frontend feature).

**Estructura del SKILL.md:** ~350 líneas / ~10 KB (denso por los gates).

### §6.5 `hubara-merger-archon/SKILL.md` (V1, obligatorio — resuelto §8 decisión 4)

**Rol:** consolida wiring_intents de N implementers paralelos en los
spinal files declarados en `.hubara/spinal-files.yaml`.

**Cargo del guide:**
- `sections/07-shared-files.md` (los kinds soportados)

**Output:** spinal files modificados + `$ARTIFACTS_DIR/merge-report.yaml`.

**Diferencia con `exoclaw-merger-archon` y la inspiración del frontend
merger:** soporta los kinds nuevos (`ts_object_entries_append` para Icon
registry, `yaml_dict_keys_append` para schema). Algoritmo idéntico
(deterministic apply de intents ordenados).

**Estructura del SKILL.md:** ~200 líneas / ~6 KB (mucho más chico que el
exoclaw merger porque hay menos kinds).

**Cuándo se invoca dentro del pipeline:** desde el orquestador
`hu-hubara-pipeline` en FASE 3 (rama multi-plugin) DESPUÉS del
`merge-fan-out-batch`, cuando dos o más sub-pipelines del batch
emitieron `wiring_intents` para el mismo spinal file. Si ningún
sub-pipeline tocó shared (caso común post-PR11), el merger se skip
automáticamente. Si solo uno tocó shared, también se skip (no hay nada
que consolidar). Solo corre cuando ≥2 emitieron intents al mismo path.

### §6.6 `review-pr-hubara` workflow + 5 agentes especializados (V1, obligatorio — resuelto §8 decisión 2)

> Este NO es un skill aparte; son 5 sub-agentes inline dentro del workflow
> `review-pr-hubara.yaml`. Cada uno es un `loop:` con `provider: claude`
> + `model: sonnet` + prompts específicos. No vive en `.claude/skills/`.

**Cargo del guide (todos los agentes lo leen):**
- `sections/01-general.md`
- Cada uno además carga la sección de su dominio:

| Agente | Rol | Lee del guide |
|---|---|---|
| `agent-deha-compliance` | Verifica R-rules de DEHA + plugin manifest hygiene | `02` + `03` + `04` + `references/deha-rules.md` |
| `agent-fsd-compliance` | Verifica FSD layering + import rules + anti-patterns | `05` + `06` + `references/fsd-rules.md` |
| `agent-plugin-system` | Verifica manifest schema + parity tests + render-compose drift | `07` + `references/manifest-schema.md` + `sections/08` |
| `agent-test-coverage` | Verifica que cada feature tiene functional test + e2e spec | `08` |
| `agent-security` | Verifica secrets no leaked + env vars en código + R-DIP violations | `02` + `09` |

**Output:** comment al PR con 5 reports consolidados (espejo de
`review-pr-frontend.yaml`).

**Auto-fix:** los 5 agentes emiten findings con severity. El workflow
intenta auto-fix CRITICAL/HIGH. Revierte si rompe tests.

**Status:** crear en V1, en PR18 junto con el merger.

---

## §7. Tabla resumen de archivos a crear (sizes y prioridades)

| Path | Size estimado | Prioridad | Quién lo escribe |
|---|---|---|---|
| `.claude/skills/hubara-architecture-guide/SKILL.md` | 5 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/01-general.md` | 12 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/02-backend-platform.md` | 10 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md` | 12 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/04-backend-agents.md` | 14 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md` | 10 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/06-frontend-plugin.md` | 8 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/07-shared-files.md` | 10 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/08-tests-and-gates.md` | 10 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/09-conventions.md` | 8 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/sections/10-cookbook.md` | 15 KB | P0 | PR12 |
| `.claude/skills/hubara-architecture-guide/references/manifest-schema.md` | 12 KB | P1 | PR12 |
| `.claude/skills/hubara-architecture-guide/references/deha-rules.md` | 11 KB | P1 | PR12 |
| `.claude/skills/hubara-architecture-guide/references/fsd-rules.md` | 8 KB | P1 | PR12 |
| `.claude/skills/hubara-architecture-guide/references/temporal-patterns.md` | 10 KB | P1 | PR12 |
| `.claude/skills/hubara-architecture-guide/examples/plugin-frontend-only.md` | 5 KB | P2 | PR12 |
| `.claude/skills/hubara-architecture-guide/examples/plugin-frontend-plus-api.md` | 5 KB | P2 | PR12 |
| `.claude/skills/hubara-architecture-guide/examples/plugin-with-worker.md` | 8 KB | P2 | PR12 |
| `.claude/skills/hubara-architecture-guide/examples/plugin-full-stack-agentic.md` | 12 KB | P2 | PR12 |
| `.claude/skills/hubara-architecture-guide/README.md` | 3 KB | P0 | PR12 |
| `.claude/skills/hubara-tech-refiner-archon/SKILL.md` | 7 KB | P0 | PR14 |
| `.claude/skills/hubara-plugin-planner-archon/SKILL.md` | 6 KB | P0 | PR15 |
| `.claude/skills/hubara-feature-planner-archon/SKILL.md` | 7 KB | P0 | PR16 |
| `.claude/skills/hubara-implementer-archon/SKILL.md` | 10 KB | P0 | PR16 |
| `.claude/skills/hubara-merger-archon/SKILL.md` | 6 KB | P0 | PR18 (V1, resuelto §8) |
| `.archon/workflows/review-pr-hubara.yaml` (5 agentes inline) | ~30 KB | P0 | PR18 (V1, resuelto §8) |
| **TOTAL** | **~240 KB** | | |

**Lectura:** el skill guide entero ocupa ~170 KB (un poco más grande que
un skill típico) pero **modular**: cada skill downstream carga sólo 1-3
secciones según su tarea. El context window que un implementer carga en
una tarea típica es ~30-50 KB (1 sección de cada categoría), bien dentro
de Sonnet 200K.

---

## §8. Validación del PR12 (cómo saber si quedó bien)

1. **Cobertura de contenido:**
   - `grep -r "TODO" .claude/skills/hubara-architecture-guide/` debe ser 0.
   - Cada sección termina con una línea de "Si necesitás X, leé Y".
2. **Coherencia con el código real:**
   - Cada path citado en el skill existe (`xargs ls -1` contra una lista
     curada de paths citados).
   - Cada snippet "canonical" parsea (Python via `ast.parse`, TS via
     `tsc --noEmit`).
3. **Densidad:**
   - Ratio palabras / línea > 8 (denso, no waffly).
   - Tablas son la unidad principal de presentación (>30% del contenido
     en tablas).
4. **Test de uso real:**
   - Tomar la HU dummy "agregar tool dummy_tool al agente sales que loguea hello".
   - Manualmente, leer las secciones que el planificator pediría leer.
   - ¿Las secciones cubren todo lo necesario para implementar la tarea?
   - Si no, falta material → iterar.

---

**Fin del blueprint del skill.** El blueprint de los workflows está en
`HUBARA_WORKFLOWS_BLUEPRINT.md`.
