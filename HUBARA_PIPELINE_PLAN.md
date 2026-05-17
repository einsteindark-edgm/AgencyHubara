# Plan — Pipeline Archon + Skill unificado para AgencyHubara (post-PR11)

> **Status:** propuesta inicial (2026-05-16). Sin ejecutar.
> **Audiencia:** operador del repo + cualquier futuro implementer (humano o Archon).
> **Trigger:** la arquitectura post-PR11 (manifest = SSoT, `render-compose.py`,
> AST scan de vault-capturing modules) ya permite editar plugins en paralelo
> sin tocar archivos compartidos. Ahora necesitamos un **pipeline** que
> *explote* eso y un **skill unificado** que centralice el conocimiento de
> arquitectura para que cada nodo cargue sólo la sección que necesita.
>
> **Documentos relacionados:**
> - `ARCHITECTURE.md` — fuente de verdad de la arquitectura (lee §1, §3, §7, §14 antes de este plan).
> - `PLUGIN_REFACTOR_PLAN.md` §9 — manifest como SSoT.
> - `.archon/workflows/README.md` — pipeline exoclaw existente (referencia de patrón).
> - `.archon/workflows/README-frontend.md` — pipeline frontend existente (referencia de pipeline lineal auto).

---

## §0. TL;DR — la propuesta en 60 segundos

1. **Un solo skill grande**: `.claude/skills/hubara-architecture-guide/` con
   `SKILL.md` (entrada + nav) + `sections/01..10.md` (cada sección self-contained).
   Cada nodo del pipeline pide al implementer "leé `sections/03-backend-plugin.md`"
   en vez de tener un mega-prompt repetido.

2. **5 skills delgados** del pipeline que solo orquestan + apuntan a las
   secciones del guide: `hubara-tech-refiner-archon`, `hubara-plugin-planner-archon`,
   `hubara-feature-planner-archon`, `hubara-implementer-archon`,
   `hubara-merger-archon` (último opcional, solo si la HU toca shared files).

3. **Pipeline de dos niveles**:
   - **Nivel A — plugin-level**: la HU se descompone en *plugin-batches* (cada
     batch = trabajo en un plugin distinto). Plugins ortogonales corren en
     paralelo en worktrees separados. Es el paralelismo principal.
   - **Nivel B — feature-level** (dentro de cada plugin): el sub-pipeline de
     un plugin hace plan + implementación SECUENCIAL de slices internos
     (mismo modelo que `hu-frontend-pipeline`). No hay merger interno porque
     un solo agente edita el plugin a la vez.

4. **3 workflows nuevos**:
   - `idea-a-hu-hubara` — entrada (idea → Issue), espejo de `idea-a-hu-frontend`.
   - `hu-hubara-pipeline` — super-orquestador (plugin-level + fan-out + validación + PR).
   - `hu-hubara-plugin-pipeline` — sub-pipeline por plugin (feature-level + impl).
   - (Opcional 4º: `review-pr-hubara` — code review post-PR con 5 agentes especializados).

5. **Un solo PR consolidado por HU**. El branch del super-pipeline es
   `hu/<HU_ID>`; los sub-pipelines de plugins commitean ahí mismo (rebase
   linear). Resultado: 1 PR con N plugins tocados, fácil de revisar y
   mergear con squash.

---

## §1. Por qué este diseño (y por qué no otros)

### §1.1 Alternativas consideradas

| Opción | Pros | Cons | Descartada porque |
|---|---|---|---|
| **A.** Un solo pipeline lineal igual que `hu-frontend-pipeline`, pero corriendo backend + frontend secuencial | Simple, copy-paste del existente | Cero paralelismo real (la promesa del refactor) | No cumple el objetivo |
| **B.** Plugin-level paralelo con merger automático (igual que `exoclaw-merger-archon`) | Max paralelismo + max consolidación auto | El merger es complejo y la mayoría de HUs son single-plugin (no necesita merger) | Sobre-engineering para el caso común |
| **C.** Plugin-level paralelo con N PRs separados (uno por plugin) | Cada plugin queda 100% independiente | Operador tiene que mergear N PRs en orden + se pierde la vista unificada de la HU | Fricción de review |
| **D.** Plugin-level paralelo con 1 PR consolidado (esta propuesta) | Max paralelismo, 1 PR por HU, code review unificado | Necesita un nodo de "validación final" que corra TODOS los gates juntos | Es el sweet spot |

**Ganador: D.**

### §1.2 Decisiones del diseño

| Decisión | Valor | Justificación |
|---|---|---|
| Granularidad del paralelismo | **Plugin-level** | Post-PR11 cada plugin es ortogonal (sin shared files). Es la granularidad natural. |
| Modo de ejecución | **Manual fan-out (default) + Auto-secuencial (opt-in para HU single-plugin)** | El operador decide: HU chica → auto; HU grande → fan-out controlado. |
| Merger automático | **No (de momento)** | La mayoría de HUs son single-plugin. Cuando una HU toca shared files (icono nuevo en `shared/ui`, entity nuevo cross-plugin), el merger se invoca on-demand. |
| Cuántos PRs | **1 por HU** | Todos los plugins tocados van en el mismo branch `hu/<HU_ID>`. Squash-merge final. |
| Smart resume | **Sí** | Igual que `hu-frontend-pipeline`: si una fase falla, re-lanzar el pipeline retoma desde donde quedó. |
| GitHub Project sync | **Opcional (fail-soft)** | Compatible con el config existente en `.archon/github-project-config.yaml`. |
| Branch strategy | **`hu/<HU_ID>`** | Igual que frontend pipeline. PR contra main. |

---

## §2. Inventario de "shared files" post-PR11 (qué SÍ puede conflict)

Esta tabla es la base para decidir spinal files y políticas de merge.

| Archivo | Naturaleza | Conflict si 2 HUs paralelas | Mitigación |
|---|---|---|---|
| `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` | schema YAML | sí (si ambas agregan campo nuevo) | spinal — declarar como `yaml_dict_keys_append` |
| `frontend_dashboard/scripts/plugins-sync.ts` | TypeScript validator | sí (raro) | spinal — declarar como `ts_function_body` |
| `hubara_agency/src/platform/contracts.py` | cross-plugin DTOs | sí (si ambas agregan DTO) | spinal — declarar como `python_dataclass_module` |
| `hubara_agency/src/platform/registries.py` | tool registry base | sí (raro) | spinal — declarar como `python_factory_module` |
| `hubara_agency/src/platform/tool_extensions.py` | DI invertida | sí (raro) | spinal — declarar como `python_factory_module` |
| `hubara_agency/src/platform/contracts.py` (R_JSON_FROZEN_EXEMPTIONS) | allow-list | sí (si ambas piden exemption) | spinal — declarar como `python_dict_entries_append` |
| `frontend_dashboard/src/shared/ui/Icon.tsx` | single icon registry | sí (si ambas agregan icon) | spinal — declarar como `ts_object_entries_append` (pendiente plugin-local icons) |
| `frontend_dashboard/src/shared/ui/index.ts` | barrel | sí (si ambas exportan primitive nueva) | spinal — declarar como `ts_barrel` |
| `frontend_dashboard/src/entities/<X>/index.ts` (y `api.ts`, `model.ts`, etc.) | barrel/API de entity shared | sí (si ambas agregan hook al mismo entity) | spinal — declarar como `ts_barrel` / `ts_factory_module` |
| `frontend_dashboard/src/app/providers/index.tsx` | provider chain | sí (si ambas agregan provider) | spinal — declarar como `app_provider_composition` |
| `frontend_dashboard/src/pages/Dashboard.tsx` | shell macOS | **no** (100% data-driven via PLUGINS) | NO spinal — no se edita por feature |
| `hubara_agency/docker-compose.local.yml` | autogen | mecánico (re-correr `render-compose.py`) | resolution conocida |
| `hubara_agency/uv.lock`, `frontend_dashboard/package-lock.json` | lock files | mecánico | resolution conocida |
| `PLUGIN_REFACTOR_LOG.md` | append-only | trivial | resolution conocida |
| `frontend_dashboard/src/plugins/<id>/plugin.yaml` | **manifest** | **NO** (cada plugin tiene el suyo) | parallel-safe by design |
| `hubara_agency/src/plugins/<id>/**` (Python) | tree por plugin | **NO** | parallel-safe by design |
| `frontend_dashboard/src/plugins/<id>/frontend/**` | tree por plugin | **NO** | parallel-safe by design |
| `hubara_agency/k8s/aws-produccion/worker-<name>.yaml` | manifest por worker | **NO** (file-per-worker) | parallel-safe by design |

**Lectura del cuadro:**

- **Spinal real (necesita merger o serialización):** 10 archivos shared
  globales (todos en `src/platform/`, `src/shared/`, `src/entities/`,
  `src/app/`, y los 2 archivos del manifest system).
- **Parallel-safe by design:** todo lo que vive dentro de un plugin dir
  (`<id>/...`). El 95% del código que se va a tocar en HUs típicas.
- **Conflicts mecánicos:** 3 archivos (compose, locks, log).

**Implicancia para el pipeline:** la mayoría de HUs (single-plugin) corren
SIN tocar spinal files reales. El pipeline default debe maximizar ese caso
y reservar el merger sólo para HUs que tocan shared.

---

## §3. Arquitectura del skill `hubara-architecture-guide`

### §3.1 Estructura de directorios

```
.claude/skills/hubara-architecture-guide/
├── SKILL.md                                # entry-point + tabla de secciones
├── sections/
│   ├── 01-general.md                       # vista 30k pies + plugin system + flujos
│   ├── 02-backend-platform.md              # platform/ + DEHA + R-rules + plugin_manifest
│   ├── 03-backend-plugin.md                # cómo crear plugin Python (api, agent, workers)
│   ├── 04-backend-agents.md                # workflows + activities + tools + Temporal patterns
│   ├── 05-frontend-fsd.md                  # FSD + plugin-registry + entities + features
│   ├── 06-frontend-plugin.md               # cómo crear plugin TS (manifest, sections, sidebar)
│   ├── 07-shared-files.md                  # qué es spinal, cómo no conflictar, merger contract
│   ├── 08-tests-and-gates.md               # architecture gate, premortem invariants, render-compose
│   ├── 09-conventions.md                   # naming, secrets, env vars, K8s, render-compose
│   └── 10-cookbook.md                      # patrones recurrentes (agregar tool, webhook, etc.)
├── references/
│   ├── manifest-schema.md                  # full reference del plugin.schema.yaml
│   ├── deha-rules.md                       # R-DET, R-JSON, R-STATELESS, R-HEARTBEAT, R-DIP
│   ├── fsd-rules.md                        # 4 import rules + 14 anti-patterns FSD
│   └── temporal-patterns.md                # signals, debounce, continue-as-new, patched()
├── examples/
│   ├── plugin-frontend-only.md             # ejemplo trabajado: orders, eta, agents_admin
│   ├── plugin-frontend-plus-api.md         # ejemplo trabajado: hipotético plugin "reports"
│   ├── plugin-with-worker.md               # ejemplo trabajado: catalog
│   └── plugin-full-stack-agentic.md        # ejemplo trabajado: chats (sales + remarketing)
└── README.md                                # quién soy + cómo me usan los skills downstream
```

### §3.2 Qué va en cada sección (1-2 líneas por archivo)

| Archivo | Contenido | Tamaño objetivo |
|---|---|---|
| `SKILL.md` | Frontmatter Archon + tabla "si necesitás X, leé sección Y" + lista de archivos del repo | ~5 KB |
| `sections/01-general.md` | Espejo condensado de `ARCHITECTURE.md` §1-§3: monorepo uv, FSD, plugin system, 3 loaders, manifest = SSoT | ~12 KB |
| `sections/02-backend-platform.md` | `src/platform/` capa por capa, plugin_manifest API, R-DIP, contracts.py, registries | ~10 KB |
| `sections/03-backend-plugin.md` | Templates A-D (frontend-only / +API / +worker / full-stack), checklist de archivos | ~12 KB |
| `sections/04-backend-agents.md` | Workflows (debounce, run_agent_turn, continue-as-new), activities (heartbeat, retry), tools (ToolBase, decisión vs acción), composition factories | ~14 KB |
| `sections/05-frontend-fsd.md` | Capas FSD, plugin-registry.generated.ts, code splitting, dependency-cruiser rules | ~10 KB |
| `sections/06-frontend-plugin.md` | Estructura del frontend de un plugin, sections vs sidebar, props bandejón, Icon registry | ~8 KB |
| `sections/07-shared-files.md` | Tabla del §2 de este plan + wiring_intents vocabulary + cuándo invocar merger | ~10 KB |
| `sections/08-tests-and-gates.md` | architecture gate (-m architecture), import-linter (R-DIP), premortem invariants, render-compose test, npm test:arch | ~10 KB |
| `sections/09-conventions.md` | Naming, secrets K8s, env vars, render-compose.py, K8s deployment hints | ~8 KB |
| `sections/10-cookbook.md` | "Cómo agregar X" en 10-15 patrones (tool, activity, webhook, SSE endpoint, frontend feature, etc.) | ~15 KB |

**Total esperado:** ~120 KB. Más grande que un skill típico pero **modular**:
cada sección se lee independiente. Los skills del pipeline cargan 1-3 secciones
por invocación, no las 10.

### §3.3 SKILL.md — entry-point + nav

Borrador del SKILL.md (el guide entero — el detalle de cada sección va en
`sections/`):

````markdown
---
name: hubara-architecture-guide
description: Single-source-of-truth de la arquitectura de AgencyHubara
  (DEHA backend + FSD frontend + plugin system post-PR11). Diseñado para ser
  invocado por skills del pipeline Archon (hubara-tech-refiner-archon,
  hubara-plugin-planner-archon, hubara-feature-planner-archon,
  hubara-implementer-archon, hubara-merger-archon). Cada skill especialista
  pide al implementer "leé sections/0N-<area>.md" en vez de duplicar
  contenido en su propio prompt. NO escribe código; solo retorna conocimiento.
  Triggers - invocación directa via `Read .claude/skills/hubara-architecture-guide/<file>`;
  no usar como subagent.
---

# hubara-architecture-guide — guía arquitectural unificada

## Cómo se usa

Este skill NO se invoca via `Skill tool` ni como subagent. Es un repositorio
de conocimiento que otros skills del pipeline LEEN con `Read tool` cuando
necesitan contexto para una tarea específica.

Convención de uso desde otros skills:

```python
# Dentro de hubara-implementer-archon, antes de escribir código:
Read(".claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md")
# Si la tarea toca workflows Temporal, también:
Read(".claude/skills/hubara-architecture-guide/sections/04-backend-agents.md")
```

## Tabla de secciones — leé solo la que necesitás

| Si necesitás... | Leé |
|---|---|
| Entender el repo desde cero | `sections/01-general.md` |
| Editar/extender `src/platform/` | `sections/02-backend-platform.md` |
| Crear un plugin Python nuevo (cualquier template) | `sections/03-backend-plugin.md` |
| Escribir workflow / activity / tool Temporal | `sections/04-backend-agents.md` |
| Editar/extender `frontend_dashboard/src/{entities,features,shared}/` | `sections/05-frontend-fsd.md` |
| Crear el frontend de un plugin | `sections/06-frontend-plugin.md` |
| Saber si un archivo es spinal (conflict-prone) | `sections/07-shared-files.md` |
| Diagnosticar fallas de architecture gate / invariants | `sections/08-tests-and-gates.md` |
| Saber qué env vars / secrets / K8s hints declarar | `sections/09-conventions.md` |
| Patrones recurrentes ("agregar tool nuevo", "agregar webhook") | `sections/10-cookbook.md` |
| Schema completo del `plugin.yaml` | `references/manifest-schema.md` |
| Las 5 R-rules de DEHA en detalle | `references/deha-rules.md` |
| Las 4 import rules + 14 anti-patterns FSD | `references/fsd-rules.md` |
| Patrones Temporal (signal, debounce, patched, CAN) | `references/temporal-patterns.md` |
| Ejemplo real de plugin frontend-only | `examples/plugin-frontend-only.md` |
| Ejemplo real de plugin con worker Temporal | `examples/plugin-with-worker.md` |
| Ejemplo real de plugin full-stack agéntico | `examples/plugin-full-stack-agentic.md` |

## Mapa rápido del repo

```
AgencyHubara/
├── hubara_agency/                    # backend Python (uv workspace member)
│   ├── src/main.py                   # FastAPI loader (auto-discovery)
│   ├── src/run_workers.py            # meta-launcher Temporal workers
│   ├── src/platform/                 # librería compartida cross-plugin
│   └── src/plugins/<id>/             # tu plugin Python vive acá
│
├── frontend_dashboard/               # frontend React + Vite + Tauri
│   ├── src/pages/Dashboard.tsx       # shell macOS 100% data-driven
│   ├── src/plugins/<id>/             # tu plugin frontend + manifest acá
│   ├── src/plugins/_schema/          # JSON Schema del manifest
│   └── scripts/plugins-sync.ts       # generador del registry
│
└── .archon/workflows/                # pipelines Archon (hu-hubara-pipeline, etc.)
```

## Reglas DURAS (no negociables)

1. **R-DET** (workflows determinísticos): nada de `datetime.now()` / `random` / I/O fuera de activities.
2. **R-JSON** (boundary): todo lo que cruza workflow↔activity es `@dataclass(frozen=True)` JSON-serializable.
3. **R-STATELESS** (activities): activities sin state entre llamadas.
4. **R-HEARTBEAT** (long-running): activities >10s usan `@with_heartbeat`.
5. **R-DIP** (dependency): `platform/` no importa plugins; plugins no importan plugins siblings.
6. **FSD layering**: `shared` → `entities` → `features` → `pages` → `app`. Importa solo hacia abajo.
7. **plugin manifest = SSoT**: si una conexión del plugin con el sistema no está expresable en
   `plugin.yaml`, eso es un bug del schema. Agregá el campo al schema antes que hacerlo "a mano".

Las reglas detalladas + enforcement por test viven en `references/deha-rules.md` y `references/fsd-rules.md`.
````

### §3.4 Ejemplo concreto de uso desde un skill

Cuando `hubara-implementer-archon` recibe una tarea de "agregar tool nuevo
al plugin chats", el prompt del skill incluye:

```markdown
## Step 0 — Cargá el contexto arquitectural

Antes de escribir cualquier código:

1. Leé `.claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md`
   (estructura general del plugin).
2. Leé `.claude/skills/hubara-architecture-guide/sections/04-backend-agents.md`
   (cómo escribir tools, activities, workflows compliant con DEHA).
3. Leé `.claude/skills/hubara-architecture-guide/sections/08-tests-and-gates.md`
   (qué gates van a evaluar tu PR).
4. Leé `.claude/skills/hubara-architecture-guide/sections/10-cookbook.md`
   en la entry "Agregar tool nuevo a un agente Temporal".

NO leas otras secciones. NO leas examples/ a menos que el cookbook te lo pida
explícitamente. El context window es finito.
```

### §3.5 Lo que NO va en este skill (y por qué)

- ❌ **Detalles de cómo correr el pipeline Archon** — eso va en `.archon/workflows/README-hubara.md` (igual que README.md y README-frontend.md existentes).
- ❌ **Historia del refactor** — vive en `PLUGIN_REFACTOR_LOG.md`.
- ❌ **Decisiones rechazadas** — viven en el `PLAN.md` correspondiente.
- ❌ **Comandos `git`** — los pipelines los emiten. El skill es agnóstico al transporte.

---

## §4. Arquitectura del pipeline `hu-hubara-pipeline`

### §4.1 Diagrama de fases (camino feliz, HU multi-plugin)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ idea-a-hu-hubara <idea>                                                      │
│   ├─ normalize-input                                                         │
│   ├─ refinar-hu-producto (1 pasada AI, sin loop)                             │
│   ├─ validate-hu                                                             │
│   ├─ save-draft (frontend_dashboard/.frontend/drafts/<ts>.md)                │
│   ├─ crear-issue (gh issue create --label hubara-hu)                         │
│   ├─ agregar-a-project (status: "Idea refined")                              │
│   └─ [APPROVAL GATE] gate-lanzar-pipeline                                    │
│           ↓ (aprobás)                                                        │
│           lanzar-pipeline (background)                                       │
└─────────────────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ hu-hubara-pipeline <issue-url-or-HU-id>     ← se invoca solo                 │
│                                                                              │
│ FASE 0 — Bootstrap                                                           │
│   ├─ check-prereqs (gh, npm, uv, bun, jq + .hubara/* + skills)               │
│   ├─ stage-shared-files (cp project-context, spinal-files, github-config)    │
│   ├─ resolve-input (issue url / HU id / local path)                          │
│   ├─ gen-hu-id + setup-branch (hu/<HU_ID>)                                   │
│   ├─ detect-resume-state (smart-resume: ¿qué fases ya están en main?)        │
│   └─ project-set-refining                                                    │
│                                                                              │
│ FASE 1 — Refinar técnico (skill hubara-tech-refiner-archon)                  │
│   ├─ load-refinement-if-resume (skip si ya hay refinement)                   │
│   ├─ refinar-auto (1 pasada AI; lee 01-general + 07-shared del guide)        │
│   ├─ validate-refinement (≥1 plugin afectado, ≥1 acceptance, no protected)   │
│   ├─ commit-refinement (push a hu/<HU_ID>)                                   │
│   └─ project-set-refined                                                     │
│                                                                              │
│ FASE 2 — Plan plugin-level (skill hubara-plugin-planner-archon)              │
│   ├─ load-plan-if-resume                                                     │
│   ├─ planificar-auto (1 pasada AI; emite plugin-batches con DAG)             │
│   ├─ validate-plan (≥1 batch, classify single-plugin vs multi-plugin)        │
│   ├─ commit-plan                                                             │
│   └─ project-set-planned                                                     │
│                                                                              │
│ FASE 3 — Implementación                                                      │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐    │
│   │ rama A — SINGLE-PLUGIN: corre el sub-pipeline inline (auto)         │    │
│   │   loop secuencial sobre feature-batches del único plugin            │    │
│   │   (igual estructura que hu-frontend-pipeline FASE 3)                │    │
│   └─────────────────────────────────────────────────────────────────────┘    │
│                              ── O ──                                         │
│   ┌─────────────────────────────────────────────────────────────────────┐    │
│   │ rama B — MULTI-PLUGIN: fan-out manual a sub-pipelines paralelos     │    │
│   │   ├─ print-fan-out-commands (N comandos a lanzar en N terminales)    │   │
│   │   ├─ [APPROVAL] wait-fan-out-done ("ready" cuando todos terminaron) │    │
│   │   ├─ git fetch + git merge --ff-only origin/hu/<HU_ID>              │    │
│   │   │   (recoge los commits que los sub-pipelines pushearon)          │    │
│   │   ├─ check-pipeline-error (revisa hubara-plugin-error.yaml dropped) │    │
│   │   └─ project-set-implementing                                       │    │
│   └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│ FASE 4 — Validación final consolidada                                        │
│   ├─ render-compose-check (no drift en docker-compose.local.yml)             │
│   ├─ uv pytest hubara_agency (full suite + architecture gate)                │
│   ├─ uv run lint-imports (R-DIP)                                             │
│   ├─ npm test (frontend full suite + test:arch)                              │
│   ├─ npm run build (vite + tauri config)                                     │
│   └─ playwright E2E (con FastAPI on random port, mismo patrón frontend)      │
│                                                                              │
│ FASE 5 — PR                                                                  │
│   ├─ build-pr-body (resumen + plugins tocados + playwright evidence)         │
│   ├─ gh pr create (--body-file, sin interpretación de backticks)             │
│   └─ project-set-done                                                        │
│                                                                              │
│ FASE 6 — Review automático                                                   │
│   └─ trigger-review (env -u CLAUDECODE archon workflow run review-pr-hubara) │
└─────────────────────────────────────────────────────────────────────────────┘

      ↓ (review-pr-hubara, background)
┌─────────────────────────────────────────────────────────────────────────────┐
│ review-pr-hubara <PR_URL>                                                    │
│   ├─ fetch-pr + checkout-branch + fetch-diff                                 │
│   ├─ classify (haiku decide qué de los 5 agentes correr)                     │
│   ├─ 5 agentes en paralelo (cuando aplica)                                   │
│   │   ├─ deha-compliance (R-rules backend)                                   │
│   │   ├─ fsd-compliance (FSD frontend)                                       │
│   │   ├─ plugin-system (manifest schema, parity, shared-file hygiene)        │
│   │   ├─ test-coverage (functional tests + playwright)                       │
│   │   └─ security (secrets leaked, env vars en código)                       │
│   ├─ synthesize (consolidate findings)                                       │
│   ├─ auto-fix CRITICAL/HIGH (revierte si rompe tests)                        │
│   └─ post-comment al PR                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### §4.2 Sub-pipeline `hu-hubara-plugin-pipeline <HU_ID> <plugin_id>`

Es el sub-pipeline que corre **dentro** del worktree fresh de un plugin
específico. Lo invoca el operador (en modo manual fan-out) o el orquestador
(en modo single-plugin auto).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ hu-hubara-plugin-pipeline <HU_ID> <plugin_id>                                │
│                                                                              │
│ FASE 0 — Bootstrap del plugin worktree                                       │
│   ├─ check-prereqs                                                           │
│   ├─ checkout hu/<HU_ID> (la rama base ya tiene refinement + plan committed) │
│   ├─ cargar-plugin-trabajo (del plugin-manifest.yaml extrae plugin_id work)  │
│   └─ stage-shared-files (project-context + spinal-files + el slice del plan) │
│                                                                              │
│ FASE 1 — Plan feature-level dentro del plugin                                │
│   │  (skill hubara-feature-planner-archon)                                   │
│   ├─ planificar-feature-auto (1 pasada AI sobre el plugin context)           │
│   │   Lee: 03-backend-plugin + 06-frontend-plugin + 04-backend-agents        │
│   │        del guide, según qué layers toque el plugin.                      │
│   │   Output: feature-plan-manifest.yaml + tareas/F<NN>-*.md                 │
│   ├─ validate-feature-plan (1-12 tareas, no protected files)                 │
│   └─ commit-feature-plan (al subdir frontend_dashboard/.hubara/plans/...)    │
│                                                                              │
│ FASE 2 — Implementar secuencial dentro del plugin                            │
│   │  (skill hubara-implementer-archon, mismo patrón que frontend)            │
│   └─ implementar-secuencial (loop con until_bash determinista)               │
│       ├─ AI escribe código + tests + task-result.yaml                        │
│       ├─ until_bash corre TODOS los gates aplicables:                        │
│       │   - npm test (si tocó frontend)                                      │
│       │   - npm run test:arch                                                │
│       │   - uv run pytest (los tests del plugin específico)                  │
│       │   - uv run pytest -m architecture                                    │
│       │   - uv run lint-imports                                              │
│       │   - render-compose-check si tocó manifest                            │
│       │   - playwright E2E (si tocó UI)                                      │
│       ├─ commit + push al branch hu/<HU_ID> con prefijo                      │
│       │   `${HU_ID} [${plugin_id}] ${TASK_ID}: status=${STATUS}`             │
│       └─ retry: 2 det-retries + 1 transient-retry (como frontend)            │
│                                                                              │
│ FASE 3 — Reporte de retorno al orquestador                                   │
│   └─ write-plugin-result-yaml (lo escribe a                                  │
│       hubara_agency/.hubara/results/<HU_ID>/plugin-<plugin_id>-result.yaml)  │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Punto clave del fan-out:** todos los sub-pipelines paralelos commitean al
**mismo branch `hu/<HU_ID>`**. Como cada sub-pipeline solo edita SU plugin
dir (`src/plugins/<plugin_id>/` y `frontend_dashboard/src/plugins/<plugin_id>/`),
no hay conflicts. Si dos sub-pipelines tocaran un spinal file, el pipeline
falla al hacer el `git push --rebase` y el operador re-corre con el
`hubara-merger-archon` activado.

### §4.3 Cuándo single-plugin vs multi-plugin

El `hubara-plugin-planner-archon` clasifica la HU en uno de dos modos
basándose en cuántos plugins toca el plan-manifest:

- **single-plugin**: 1 plugin afectado. El orquestador ejecuta el
  sub-pipeline inline (no fan-out, no approval). Mismo modelo que
  `hu-frontend-pipeline` actual.
- **multi-plugin**: 2+ plugins afectados. El orquestador imprime los N
  comandos de fan-out, espera approval del operador, y al volver hace el
  merge consolidado.

Esto permite que la mayoría de HUs corran 100% auto sin fricción humana, y
solo las HUs ambiciosas (las que justifican paralelismo) pidan al operador
abrir terminales.

### §4.4 Modo manual standalone (siempre disponible)

El operador puede saltarse el orquestador y correr directamente:

```bash
archon workflow run hu-hubara-plugin-pipeline "<HU_ID> <plugin_id>"
```

Útil para:
- Iterar un solo plugin después de un fallo del orquestador.
- Trabajar en un plugin nuevo sin generar HU completa.
- Debug de un plugin existente sin pasar por refinar+planificar.

---

## §5. Lista exhaustiva de artefactos a crear

### §5.1 Files nuevos en el repo

```
.claude/skills/
├── hubara-architecture-guide/
│   ├── SKILL.md
│   ├── sections/{01..10}.md
│   ├── references/{manifest-schema, deha-rules, fsd-rules, temporal-patterns}.md
│   ├── examples/{plugin-frontend-only, plugin-frontend-plus-api,
│   │             plugin-with-worker, plugin-full-stack-agentic}.md
│   └── README.md
├── hubara-tech-refiner-archon/SKILL.md
├── hubara-plugin-planner-archon/SKILL.md
├── hubara-feature-planner-archon/SKILL.md
├── hubara-implementer-archon/SKILL.md
└── hubara-merger-archon/SKILL.md          # opcional, solo si la HU toca shared

.archon/workflows/
├── README-hubara.md                       # guía operacional (espejo de README-frontend.md)
├── idea-a-hu-hubara.yaml                  # entrada idea → issue
├── hu-hubara-pipeline.yaml                # super-orquestador
├── hu-hubara-plugin-pipeline.yaml         # sub-pipeline por plugin
└── review-pr-hubara.yaml                  # code review post-PR (opcional V2)

hubara_agency/.hubara/                     # convenciones del orquestador (espejo de .exoclaw)
├── spinal-files.yaml                      # NUEVO: spinal files cross-repo
├── project-context.md                     # NUEVO: paths + commands del repo
├── refinements/<HU_ID>-{tech,original}.md # generado por pipeline
├── plans/<HU_ID>/
│   ├── plugin-manifest.yaml               # DAG plugin-level
│   ├── feature-plans/<plugin_id>/         # generado por sub-pipelines
│   │   ├── feature-plan-manifest.yaml
│   │   └── tareas/F<NN>-*.md
│   └── ...
└── results/<HU_ID>/
    └── plugin-<plugin_id>-result.yaml      # un yaml por plugin completado

frontend_dashboard/.hubara/                # mirror para frontend-side context (solo si necesario)
├── (espejo opcional — TBD si vale la pena dividir o usar el de hubara_agency)
```

### §5.2 Files existentes que modificar (ninguno hard, todos opt-in)

- `frontend_dashboard/.frontend/` y `hubara_agency/.exoclaw/` — quedan
  intactos. El pipeline nuevo coexiste; no rompe los existentes.
- `ARCHITECTURE.md` — agregar §15 "Pipeline Hubara (DEHA + FSD + plugin-aware)"
  apuntando a este plan y al README-hubara.md.

### §5.3 Convenciones nuevas

#### Spinal files de Hubara (`hubara_agency/.hubara/spinal-files.yaml`)

Espejo del `.exoclaw/spinal-files.yaml` actual pero ampliado para cubrir
los shared files del §2 de este plan:

```yaml
version: 1

spinal_files:
  # ── Manifest system (cross-plugin) ────────────────────────────────────
  - path: frontend_dashboard/src/plugins/_schema/plugin.schema.yaml
    kind: yaml_dict_keys_append
    anchors:
      properties_block: "^properties:"

  - path: frontend_dashboard/scripts/plugins-sync.ts
    kind: ts_function_body
    note: |
      Raramente se toca. Si tu HU necesita extender el validator,
      mark task as blocked → operador hace ADR.

  # ── Backend platform (cross-plugin DTOs y registries) ──────────────
  - path: hubara_agency/src/platform/contracts.py
    kind: python_dataclass_module
  - path: hubara_agency/src/platform/registries.py
    kind: python_factory_module
  - path: hubara_agency/src/platform/tool_extensions.py
    kind: python_factory_module
  - path: hubara_agency/src/platform/constants.py
    kind: python_constants_module
    note: |
      Post-PR11 solo contiene ROUTE_* y WHATSAPP_SESSION_PREFIX.
      Si vas a agregar una constante per-plugin → bug del manifest schema, no acá.

  # ── Frontend shared layer ─────────────────────────────────────────
  - path: frontend_dashboard/src/shared/ui/Icon.tsx
    kind: ts_object_entries_append
    note: |
      Plugin-local icons es deferred. Hasta entonces, agregar al registry acá.
  - path: frontend_dashboard/src/shared/*/index.ts
    kind: ts_barrel
  - path: frontend_dashboard/src/app/providers/index.tsx
    kind: app_provider_composition

  # ── Frontend entities shared (cuando 2+ plugins agregan al mismo entity) ──
  - path: frontend_dashboard/src/entities/*/index.ts
    kind: ts_barrel
  - path: frontend_dashboard/src/entities/*/api.ts
    kind: ts_factory_module
  - path: frontend_dashboard/src/entities/*/model.ts
    kind: ts_dataclass_module
  - path: frontend_dashboard/src/entities/*/contracts.ts
    kind: ts_dataclass_module
  - path: frontend_dashboard/src/entities/*/keys.ts
    kind: ts_factory_module

# Behavior:
#   - Archivos dentro de src/plugins/<id>/ NO son spinal — el plugin entero
#     es del task que lo crea/edita.
#   - K8s manifests file-per-worker (worker-<name>.yaml) NO son spinal.
#   - docker-compose.local.yml NO es spinal — se auto-genera.

# Files NEVER spinal (operator-owned):
#   - hubara_agency/k8s/aws-produccion/api-deployment.yaml
#   - hubara_agency/run_api.py
#   - hubara_agency/src/main.py
#   - hubara_agency/src/run_workers.py
#   - hubara_agency/pyproject.toml, uv.lock
#   - frontend_dashboard/package.json, vite.config.ts, tsconfig*.json
#   - frontend_dashboard/src/main.tsx
```

#### Project context (`hubara_agency/.hubara/project-context.md`)

Espejo de los `.exoclaw/project-context.md` y `.frontend/project-context.md`
existentes pero cross-repo:

```markdown
# Hubara — project context (para skills del pipeline)

## Layout

- Repo root: /Users/edgm/Documents/Projects/AgencyHubara (cwd del operador)
- Backend Python: hubara_agency/src/
- Frontend TS: frontend_dashboard/src/
- Plugins frontend (manifests): frontend_dashboard/src/plugins/<id>/
- Plugins backend (Python): hubara_agency/src/plugins/<id>/

## Comandos canónicos (CWD = repo root salvo nota)

### Backend
- Boot API: `cd hubara_agency && uv run python run_api.py`
- Workers all: `cd hubara_agency && uv run python -m src.run_workers`
- Tests: `cd hubara_agency && uv run pytest`
- Architecture gate: `cd hubara_agency && uv run pytest -m architecture`
- Import-linter (R-DIP): `cd hubara_agency && uv run lint-imports`
- Render compose: `cd hubara_agency && uv run python scripts/render-compose.py`
- Get task queue: `cd hubara_agency && uv run python -c "from src.platform.plugin_manifest import get_task_queue; print(get_task_queue('<plugin>', '<worker>'))"`

### Frontend
- Dev server: `cd frontend_dashboard && npm run dev`
- Sync plugins: `cd frontend_dashboard && npm run plugins:sync`
- Tests: `cd frontend_dashboard && npm test`
- Architecture gate: `cd frontend_dashboard && npm run test:arch`
- Build: `cd frontend_dashboard && npm run build`
- Playwright E2E: `cd frontend_dashboard && npx playwright test`

### Combined (smoke check para PR)
- `cd hubara_agency && uv run pytest tests/plugins/ -q` (premortem invariants)
- `cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code hubara_agency/docker-compose.local.yml`

## Naming conventions

- HU id: `HU-<YYYYMMDD>-<HHMMSS>-<slug>` (igual que frontend pipeline)
- Branch: `hu/<HU_ID>`
- Plugin id: `^[a-z][a-z0-9_]*$` (snake_case, NO guion medio)
- Task queue: `^queue-[a-z][a-z0-9-]*$` (prefijo `queue-`)
- Worker name: lowercase, palabra única o snake_case (`sales`, `remarketing`, `catalog_sync`)
- K8s deployment: `worker-<name>.yaml` (matchea el name del manifest)

## Paths de PYTHONPATH

- `from src.platform...` resuelve desde `hubara_agency/` (PYTHONPATH base).
- `from src.plugins.<id>...` resuelve igual.
- NO usar `from hubara_agency.src...` desde código del repo (solo `import hubara_agency.src...` desde tests específicos).
```

---

## §6. Plan de implementación (cómo construir esto)

Propuesta de PRs incrementales, cada uno entregable y testeable.

| PR | Alcance | Entregables | Validación |
|---|---|---|---|
| **PR12** | Skill `hubara-architecture-guide` completo | 10 secciones + 4 references + 4 examples + SKILL.md + README | Lectura humana: ¿hay claridad? + grep "TODO" en secciones (debe ser 0) |
| **PR13** | Convenciones `.hubara/` | `spinal-files.yaml` + `project-context.md` | Schema-validate yaml + script smoke que parsea ambos |
| **PR14** | Skill `hubara-tech-refiner-archon` + workflow `idea-a-hu-hubara` | SKILL.md + workflow + label `hubara-hu` en GitHub | Corrida end-to-end con idea fake → issue creado |
| **PR15** | Skill `hubara-plugin-planner-archon` + parte del orquestador FASE 1-2 | SKILL.md + nodos refinar-auto / planificar-auto del `hu-hubara-pipeline.yaml` | Corrida: idea fake → refinement + plan-manifest en hu/<HU_ID> |
| **PR16** | Skills `hubara-feature-planner-archon` + `hubara-implementer-archon` + workflow `hu-hubara-plugin-pipeline` | 2 SKILLs + 1 workflow | Corrida: tomar 1 plugin del plan y correr el sub-pipeline solo → commit a hu/<HU_ID> |
| **PR17** | Orquestador `hu-hubara-pipeline` completo (FASE 3-5) | Workflow completo con rama single-plugin auto y rama multi-plugin fan-out | Corrida E2E con HU single-plugin (auto) y HU multi-plugin (fan-out manual) |
| **PR18** | Skill `hubara-merger-archon` + workflow `review-pr-hubara` con 5 agentes (V1 obligatorio) | 1 SKILL + 1 workflow + 5 agent prompts inline | Corrida E2E sobre PR de PR17 + corrida de merger sobre batch que tocó shared file |
| **PR19** (post-validación) | Deprecación de `exoclaw-*`, `frontend-*` skills + workflows + `.exoclaw/`, `.frontend/` convenciones | Eliminación de archivos + actualizar `ARCHITECTURE.md` § historia | Validación previa: 3+ HUs reales mergeadas con hubara pipeline sin fallback a legacy |

### §6.1 Orden lógico de implementación (RESUELTO — scope V1)

```
PR12 (skill guide completo)                 ← foundation, no rompe nada
   ↓
PR13 (convenciones .hubara/)                ← infra, no rompe nada
   ↓
PR14 (refiner + idea-a-hu)                  ← entry point, primer E2E parcial
   ↓
PR15 (plugin-planner + orquestador refinar+planificar) ← orquestador toma forma
   ↓
PR16 (feature-planner + implementer + sub-pipeline) ← single-plugin mode funciona
   ↓
PR17 (orquestador completo con fan-out + validación + PR) ← multi-plugin mode funciona
   ↓
PR18 (merger + review automático)           ← cierra el ciclo V1 (decisión 4 + 5 del §8)
   ↓
═════════════════════════════════════════════════════════════════
   (validación con 3+ HUs reales en producción)
═════════════════════════════════════════════════════════════════
   ↓
PR19 (deprecation de exoclaw + frontend)    ← cleanup post-validación (decisión 5 del §8)
```

Cada PR es ~1-3 días de trabajo focused. PR18 cierra el scope V1 (con
review automático y merger ya operativos). PR19 se posterga hasta tener
evidencia empírica de que el hubara cubre todos los casos.

### §6.2 Riesgos identificados (premortem rápido)

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| El skill `hubara-architecture-guide` queda demasiado grande y los implementers cargan mucho contexto | media | medio | secciones modulares + nav explícito en SKILL.md + límite de 3 secciones por skill downstream |
| El fan-out manual (multi-plugin) confunde al operador | baja | medio | imprimir comandos copy-pasteables + ejemplo en README-hubara.md |
| Sub-pipelines paralelos commitean al mismo branch y se pisan en `git push` | alta | bajo | retry con `pull --rebase` igual que frontend pipeline ya tiene |
| El planner mete tasks de plugin distinto en mismo batch por error | media | medio | invariant test en `validate-plan` que verifica batch homogéneo |
| Falla de `render-compose-check` no detectada en sub-pipeline (solo se detecta en FASE 4) | alta | bajo | agregar el check al until_bash del sub-pipeline cuando la tarea tocó manifest |
| La rama `hu/<HU_ID>` queda gigante por commits granulares y el squash final pierde info | media | bajo | el body del PR (build-pr-body) consolida los commits en una tabla legible |
| El operador olvida que `.hubara/` debe estar committeado en main antes de la primera corrida | alta | alto | bootstrap check explícito en FASE 0 + README-hubara.md §3 destaca el setup inicial |

---

## §7. Comparación con pipelines existentes (qué reusamos, qué inventamos)

| Capability | exoclaw pipeline | frontend pipeline | hubara pipeline (este) |
|---|---|---|---|
| Entry point con GitHub Issue | no | sí (`idea-a-hu-frontend`) | **sí** (`idea-a-hu-hubara`) |
| Modo auto secuencial | no | sí (`hu-frontend-pipeline`) | **sí** (single-plugin) |
| Modo fan-out paralelo | sí (manual terminales) | no | **sí** (multi-plugin) |
| Merger automático | sí (`exoclaw-merger-archon`) | no | **opcional** (solo si shared files tocados — V2) |
| Smart resume | parcial | sí | **sí** (heredado del pattern frontend) |
| GitHub Project sync | no | sí (fail-soft) | **sí** (compatible con config existente) |
| Architecture gate determinista | sí (`-m architecture`) | sí (`npm run test:arch`) | **sí** (ambos) |
| Playwright E2E gate | no | sí (random port + auto cleanup) | **sí** (heredado, gate del FASE 4) |
| Render-compose check | n/a | n/a | **nuevo** (necesario post-PR11) |
| Import-linter (R-DIP) | sí (gate) | n/a | **sí** (gate) |
| Skill arquitectura unificado | no (4 skills separados) | no (4 skills separados) | **sí** (1 skill grande + 5 delgados) |
| Cuántos PRs por HU | varía | 1 | **1** (consolidado) |
| Code review automático | no | sí (`review-pr-frontend`) | **sí** (V2) |
| Workflow lineal con `until_bash` determinista | parcial | sí (canónico) | **sí** (heredado) |

**Resumen:** el pipeline hubara es la **unión** de los dos existentes con
extras específicos del plugin system (manifest validation, render-compose
check, plugin-level fan-out). El skill unificado es lo verdaderamente
nuevo.

---

## §8. Decisiones del operador (RESUELTAS — 2026-05-17)

> Las 5 decisiones abiertas iniciales fueron resueltas. Esta sección las
> registra para referencia histórica.

| # | Decisión | Resolución | Implicancia |
|---|---|---|---|
| 1 | Branch strategy para fan-out paralelo | **Mismo branch `hu/<HU_ID>`** (pull-rebase retry como frontend) | Sin cambio al plan. Spec ya escrita asume mismo branch. |
| 2 | Code review automático: V1 o V2 | **V1 (ahora, incluido en scope inicial)** | **+1 PR al scope: PR18 (`review-pr-hubara`)**. Replicar patrón de `review-pr-frontend.yaml` adaptado a hubara (5 agentes: deha-compliance, fsd-compliance, plugin-system, test-coverage, security). |
| 3 | Scope del skill `hubara-architecture-guide` en PR12 | **TODO (10 secciones + 4 refs + 4 examples)** | Sin cambio al plan. Spec ya asume PR12 completo. |
| 4 | `hubara-merger-archon` en V1 o V2 | **V1 (ahora, junto con el pipeline)** | **Cambio al plan:** subir el merger de "P2/V2 opcional" a "P0/V1 obligatorio". El sub-pipeline puede dejar wiring_intents en `task-result.yaml` desde el día 1, y el orquestador invoca el merger cuando 2+ tareas paralelas tocan el mismo spinal file. |
| 5 | Pipelines `exoclaw` y `frontend` legacy | **Reemplazar (deprecate)** cuando hubara esté estable | **+1 PR al scope: PR19 (deprecation)**. Eliminar workflows + skills legacy + convenciones `.exoclaw/` y `.frontend/`. Se ejecuta DESPUÉS de que PR12-PR18 estén mergeados y validados con HUs reales. |

### §8.1 Scope final (post-decisiones)

PR12 → PR18 = scope V1 inicial (sin PR19 que es post-validación).

| PR | Alcance | Status |
|---|---|---|
| **PR12** | Skill `hubara-architecture-guide` completo | scope sin cambio |
| **PR13** | Convenciones `.hubara/` (spinal-files + project-context) | scope sin cambio |
| **PR14** | Skill `hubara-tech-refiner-archon` + workflow `idea-a-hu-hubara` | scope sin cambio |
| **PR15** | Skill `hubara-plugin-planner-archon` + FASE 1-2 del orquestador | scope sin cambio |
| **PR16** | Skills `hubara-feature-planner-archon` + `hubara-implementer-archon` + workflow `hu-hubara-plugin-pipeline` | scope sin cambio |
| **PR17** | Orquestador `hu-hubara-pipeline` completo (FASE 3-5) | scope sin cambio |
| **PR18 (V1)** | **Skill `hubara-merger-archon` + workflow `review-pr-hubara` con 5 agentes** | **NUEVO en scope V1** (antes era V2) |
| **PR19 (post-validación)** | **Deprecación de pipelines exoclaw y frontend** | **NUEVO**, fuera del scope V1 inicial — se ejecuta tras N HUs reales sobreviviendo en el pipeline hubara sin regresar a los legacy. |

### §8.2 Por qué resolver así (recap del razonamiento del operador)

- **Mismo branch (1):** ya validado por el patrón frontend, simple.
- **Review en V1 (2):** el operador prioriza una herramienta completa de
  arranque sobre velocidad inicial. Trade-off aceptado.
- **Skill completo en PR12 (3):** alinea con "no liberar a medias".
- **Merger en V1 (4):** habilita HUs multi-plugin con shared files desde
  el día 1, sin marcar tareas como `blocked: requires_merger`. Más completo.
- **Reemplazar legacy (5):** repo más limpio a largo plazo. El criterio de
  "estable" para disparar PR19 = "3+ HUs cerradas exitosamente con el
  pipeline hubara, sin fallback a exoclaw/frontend".

### §8.3 Cambios concretos al plan (qué editar en los blueprints)

- `HUBARA_SKILL_BLUEPRINT.md §6.5` → subir `hubara-merger-archon` de "V2 opcional" a "V1 obligatorio". Spec del SKILL.md ya escrita, solo cambiar status.
- `HUBARA_SKILL_BLUEPRINT.md §7` → tabla agregar 5 agentes del review (`agent-deha-compliance`, `agent-fsd-compliance`, `agent-plugin-system`, `agent-test-coverage`, `agent-security`).
- `HUBARA_WORKFLOWS_BLUEPRINT.md` → agregar §5 "Workflow review-pr-hubara" + §6 "PR19 deprecation plan".

---

## §9. Próximos pasos accionables

Si aprobás este plan:

1. **Validá las decisiones abiertas del §8** (5 minutos).
2. **Confirmá los nombres** (¿te gusta `hubara-architecture-guide`? ¿`hu-hubara-pipeline`?). Cambiar nombres al inicio es barato; cambiarlos después es doloroso.
3. **PR12 (skill guide)** — el primer entregable real. Sin él, los pipelines no tienen dónde apuntar.

Después PR12 entregado, podés iterar el resto en cualquier orden (PR13-PR18)
porque cada uno es agnóstico del anterior salvo:
- PR15 requiere PR12 (planner referencia el guide).
- PR16 requiere PR14+15 (implementer necesita el plan del planner).
- PR17 requiere PR15+16 (orquestador hace fan-out a sub-pipelines).
- PR18 requiere PR17 (review corre sobre PRs creados por el pipeline).

---

## §10. Apéndices

### §10.1 Blueprints detallados de cada artefacto

Los blueprints completos viven en archivos separados para no inflar este
plan:

- `HUBARA_SKILL_BLUEPRINT.md` — estructura completa del skill guide con
  table-of-contents de cada sección.
- `HUBARA_WORKFLOWS_BLUEPRINT.md` — pseudocódigo (no YAML completo, solo
  estructura de nodos + invocaciones de skills) de los 3 workflows.

Estos los escribo a continuación como deliverables anexos.

### §10.2 Glosario

- **Plugin** — unidad autocontenida con frontend / API / agente Temporal en hasta 3 stacks.
- **HU** — Historia de Usuario (acceptance criteria + out-of-scope + tone).
- **DAG plugin-level** — descomposición de la HU en trabajos por plugin (Nivel A).
- **DAG feature-level** — descomposición del trabajo de un plugin en slices (Nivel B).
- **Plugin-batch** — set de plugins que pueden ejecutarse en paralelo (sin deps mutuas).
- **Feature-batch** — set de slices dentro de un plugin sin deps mutuas (raramente >1 en hubara porque dentro del plugin los slices suelen ser secuenciales).
- **Single-plugin HU** — HU cuyo plan-manifest tiene 1 plugin afectado. Corre auto inline.
- **Multi-plugin HU** — HU cuyo plan-manifest tiene 2+ plugins. Necesita fan-out manual.
- **Spinal file** — archivo shared cross-task que N tareas paralelas podrían modificar (necesita merger o serialización).
- **Worktree** — copia aislada del repo que Archon crea por `archon run`. Cada sub-pipeline tiene el suyo.
- **Smart resume** — capacidad del pipeline de detectar fases ya completadas (refinement / plan committeados a main o branch) y saltarlas en re-lanzamientos.

### §10.3 Inspiración (qué tomamos de cada pipeline existente)

**De `exoclaw-temporal` (`README.md`):**
- Modelo de manual fan-out de N terminales para paralelismo real.
- Separación skill planner ↔ implementer (no un mega-prompt).
- `wiring_intents` para metadata declarativa de spinal files.
- `spinal-files.yaml` convención per-dominio.
- Pattern de "deterministic nodes for determinist work, AI nodes for reasoning".

**De `hu-frontend-pipeline` (`README-frontend.md`):**
- Entry point con GitHub Issue + Project sync.
- Pipeline lineal auto con smart-resume.
- Branch `hu/<HU_ID>` + un solo PR final.
- `until_bash` determinista corriendo gates después de cada iter del AI.
- Det-retry vs transient-retry distinguidos.
- Playwright E2E con FastAPI background en puerto random.
- Code review post-PR con 5 agentes especializados.
- Auto-fix CRITICAL/HIGH con revert si rompe tests.

**Mejoras propias del pipeline Hubara:**
- Pipeline de **dos niveles** (plugin → feature) que el frontend no tiene.
- Skill **unificado** de arquitectura cargado por sección (los otros tienen
  skills monolíticos con prompt repetido).
- Render-compose check como gate (nuevo post-PR11).
- Premortem invariants integrados al architecture gate.
- Modo mixto (auto para single-plugin, fan-out para multi-plugin) — los
  otros son uno u otro.

---

**Fin del PLAN.** Los blueprints detallados (skill + workflows) están en los
archivos hermanos `HUBARA_SKILL_BLUEPRINT.md` y `HUBARA_WORKFLOWS_BLUEPRINT.md`.
