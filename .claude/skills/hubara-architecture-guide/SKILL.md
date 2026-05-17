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

# hubara-architecture-guide — guía arquitectural unificada

> **Versión:** 1.0 (PR12, 2026-05-17).
> **Sincronía con código:** snapshot post-PR11 (manifest = SSoT).
> **Fuente de verdad humana:** `ARCHITECTURE.md` (60 KB). Este skill es la
> **versión modular y orientada-a-agente** de ese mismo conocimiento.

---

## §1. Cómo se usa este skill

Este skill **NO se invoca via `Skill tool` ni como subagent**. Es un
repositorio de conocimiento que otros skills del pipeline LEEN con `Read
tool` cuando necesitan contexto para una tarea específica.

**Patrón canónico desde otros skills:**

```python
# Dentro de hubara-implementer-archon, antes de escribir código:
Read(".claude/skills/hubara-architecture-guide/SKILL.md")           # siempre primero
Read(".claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md")
# Si la tarea toca workflows Temporal, también:
Read(".claude/skills/hubara-architecture-guide/sections/04-backend-agents.md")
```

**Por qué este patrón:**

- El skill grande no se carga entero (~170 KB). Cada skill downstream
  carga solo 1-3 secciones según su rol, mantiene context window chico.
- Hay UN solo lugar donde se documenta cada cosa. Si el repo cambia, se
  edita una sección — no 5 skills.
- Skills especialistas (refiner, planner, implementer, merger) quedan
  delgados (5-10 KB cada uno) y delegan razonamiento arquitectural a este
  guide.

---

## §2. Tabla de secciones — leé solo la que necesitás

| Si necesitás… | Leé |
|---|---|
| Entender el repo desde cero | `sections/01-general.md` |
| Editar/extender `src/platform/` | `sections/02-backend-platform.md` |
| Crear un plugin Python nuevo (cualquier template) | `sections/03-backend-plugin.md` |
| Escribir workflow / activity / tool Temporal | `sections/04-backend-agents.md` |
| Editar/extender `frontend_dashboard/src/{entities,features,shared}/` | `sections/05-frontend-fsd.md` |
| Crear el frontend de un plugin | `sections/06-frontend-plugin.md` |
| Saber si un archivo es "spinal" (conflict-prone) | `sections/07-shared-files.md` |
| Diagnosticar fallas de architecture gate / invariants | `sections/08-tests-and-gates.md` |
| Saber qué env vars / secrets / K8s hints declarar | `sections/09-conventions.md` |
| Patrones recurrentes ("agregar tool nuevo", "agregar webhook", …) | `sections/10-cookbook.md` |
| Schema completo del `plugin.yaml` | `references/manifest-schema.md` |
| Las 5 R-rules de DEHA en detalle (con tests + ejemplos válidos/inválidos) | `references/deha-rules.md` |
| Las 4 import rules + 14 anti-patterns de FSD | `references/fsd-rules.md` |
| Patrones Temporal (signals, debounce, continue-as-new, patched) | `references/temporal-patterns.md` |
| Ejemplo real de plugin frontend-only (`orders`, `eta`, `agents_admin`) | `examples/plugin-frontend-only.md` |
| Ejemplo trabajado de plugin con API pero sin worker | `examples/plugin-frontend-plus-api.md` |
| Ejemplo real de plugin con worker Temporal (`catalog`) | `examples/plugin-with-worker.md` |
| Ejemplo real de plugin full-stack agéntico (`chats`) | `examples/plugin-full-stack-agentic.md` |

**Regla de oro:** si tu task pinta backend + frontend cross-stack, leé
ambas secciones (02-04 + 05-06). Si pinta solo uno, leé solo ese. **No
cargues más de 3 secciones por task** salvo que sea estrictamente
necesario.

---

## §3. Mapa rápido del repo

```
AgencyHubara/
├── hubara_agency/                    # backend Python (uv workspace member)
│   ├── run_api.py                    # entrypoint uvicorn → src.main:app
│   ├── docker-compose.local.yml      # auto-generado por scripts/render-compose.py
│   ├── docker-compose.base.yml       # fijo: db, temporal, litellm, api, frontend
│   ├── .importlinter                 # contratos R-DIP (DEHA)
│   ├── .hubara/                      # convenciones del pipeline (spinal-files, project-context)
│   ├── k8s/aws-produccion/           # K8s manifests (1 deployment por worker)
│   ├── scripts/
│   │   ├── render-compose.py         # autogen del docker-compose.local.yml desde manifests
│   │   └── trigger_catalog_sync.py   # debug: dispara catalog workflow manual
│   └── src/
│       ├── main.py                   # FastAPI loader (auto-discovery vía plugin.yaml)
│       ├── run_workers.py            # meta-launcher Temporal (asyncio.gather sobre workers)
│       ├── platform/                 # librería compartida cross-plugin (no es plugin)
│       │   ├── plugin_manifest.py    # API de lectura de manifests (get_task_queue, etc.)
│       │   ├── contracts.py          # DTOs cross-boundary (TransferDecision, etc.)
│       │   ├── workflow_helpers.py   # run_agent_turn + PendingMessage + coalesce_pending
│       │   ├── tool_extensions.py    # registro de tools por dominio (DI invertida)
│       │   ├── temporal/             # client + dispatcher + activities + heartbeat + retry
│       │   ├── whatsapp/             # client + activities (send_message, typing_indicator)
│       │   ├── session_history/      # JSONL store + activities
│       │   ├── catalog/              # CatalogPort + LocalSnapshot reader
│       │   ├── medusa/               # MedusaJS HTTP client live
│       │   └── tools/                # tools compartidas (TransferToSalesAgent, EscalateToHuman)
│       │
│       └── plugins/                  # DOMINIO ─ los plugins Python
│           ├── chats/                # plugin agéntico (template D: full-stack)
│           ├── catalog/              # plugin con worker (template C)
│           ├── agents_admin/         # plugin frontend-only (template A)
│           ├── eta/                  # plugin frontend-only (template A)
│           └── orders/               # plugin frontend-only (template A)
│
├── frontend_dashboard/               # frontend React + Vite + Tauri (uv NO aplica acá)
│   ├── package.json                  # scripts: predev/prebuild → plugins:sync
│   ├── vite.config.ts                # alias @ → ./src, @plugins → ./src/plugins
│   ├── .dependency-cruiser.cjs       # contratos FSD + plugin isolation
│   ├── .frontend/                    # convenciones del pipeline frontend legacy
│   ├── scripts/
│   │   └── plugins-sync.ts           # generador del plugin-registry.generated.ts
│   └── src/
│       ├── pages/Dashboard.tsx       # shell macOS 100% data-driven
│       ├── app/
│       │   ├── providers/            # AppProviders chain
│       │   └── plugin-registry.generated.ts   # AUTOGEN, gitignored
│       ├── shared/                   # primitivas UI + lib + api + config
│       ├── entities/                 # dominio shared cross-plugin (chat, order, agent, …)
│       └── plugins/                  # PLUGINS frontend + MANIFESTS
│           ├── _schema/
│           │   └── plugin.schema.yaml   # JSON Schema del manifest
│           ├── chats/
│           │   ├── plugin.yaml          # MANIFEST (única fuente de verdad)
│           │   └── frontend/            # ChatsSection.tsx + features/
│           ├── catalog/
│           ├── orders/
│           ├── eta/
│           └── agents_admin/
│
└── .archon/workflows/                # pipelines Archon
    ├── README-hubara.md              # guía operacional del pipeline hubara
    ├── idea-a-hu-hubara.yaml
    ├── hu-hubara-pipeline.yaml
    ├── hu-hubara-plugin-pipeline.yaml
    └── review-pr-hubara.yaml
```

**Layout clave para memorizar:**

- **Manifest** vive en `frontend_dashboard/src/plugins/<id>/plugin.yaml`,
  no del lado backend. Los loaders Python leen ese archivo (path relativo
  desde `hubara_agency/`).
- **Plugin Python** vive en `hubara_agency/src/plugins/<id>/`.
- **Plugin frontend** vive en `frontend_dashboard/src/plugins/<id>/frontend/`.
- Los 3 stacks (frontend / API / workers) descubren plugins escaneando el
  mismo `frontend_dashboard/src/plugins/*/plugin.yaml`.

---

## §4. Reglas DURAS (no negociables)

7 reglas. Si te encontrás violando una, **el diseño está mal — no la
regla**.

| # | Regla | Significa | Enforcement |
|---|---|---|---|
| 1 | **R-DET** | Workflows determinísticos: nada de `datetime.now()` / `random` / I/O directo en `workflows/*.py` | Code review + parcial via `@workflow.defn` runtime checks |
| 2 | **R-JSON** | Todo lo que cruza `workflow.execute_activity(...)` o `client.start_workflow(...)` es `@dataclass(frozen=True)` JSON-serializable. No Pydantic, no `pathlib.Path`, no `datetime` | `test_r_json.py` (AST scan) — falla en CI |
| 3 | **R-STATELESS** | Activities sin estado entre llamadas. Cero `_CACHE = {}` / `_REGISTRY = []` a nivel módulo dentro de `activities/` | Convention + grep en architecture suite |
| 4 | **R-HEARTBEAT** | Activities con worst-case >10s usan `@with_heartbeat(every=10)` | Convention + `R_HEARTBEAT_EXEMPTIONS` allow-list |
| 5 | **R-DIP** | `src/platform/` NO importa `src/plugins/...`. Plugins NO importan plugins siblings. `tools/*.py` NO importa `temporalio.client/worker`. `parsers.py` NO importa libs HTTP | `import-linter` 4 contratos (`hubara_agency/.importlinter`) |
| 6 | **FSD layering** | `shared → entities → features → pages → app`. Importa SOLO hacia abajo. Cross-plugin frontend imports prohibidos | `dependency-cruiser` (`frontend_dashboard/.dependency-cruiser.cjs`) |
| 7 | **plugin manifest = SSoT** | Si una conexión del plugin con el sistema no se puede expresar en `plugin.yaml`, eso es **bug del schema**. Agregá el campo al schema antes que editar archivo shared | `tests/plugins/test_premortem_invariants.py` (parity tests) |

Detalle exhaustivo de cada regla + ejemplos válidos/inválidos:
- DEHA (R-DET / R-JSON / R-STATELESS / R-HEARTBEAT / R-DIP) → `references/deha-rules.md`.
- FSD (4 import rules + 14 anti-patterns) → `references/fsd-rules.md`.
- Tests que enforzan → `sections/08-tests-and-gates.md`.

---

## §5. Anti-patterns top-5 (los errores más comunes)

| # | Error | Por qué no | Qué hacer en su lugar |
|---|---|---|---|
| 1 | Agregar queue nueva editando `src/platform/constants.py` | Post-PR11 las queues viven en `plugin.yaml`. Edit a constants causa merge conflict cross-plugin | Declarar `agent.workers[].task_queue` en el manifest del plugin |
| 2 | Editar `docker-compose.local.yml` a mano | Es auto-generado por `scripts/render-compose.py` desde manifests | Editar `agent.workers[].compose` del manifest + `uv run python scripts/render-compose.py` |
| 3 | Importar `from src.plugins.chats.tools import ...` desde otro plugin | Viola R-DIP (cross-plugin import) — falla `lint-imports` | Si necesitás la tool, declararla `register_tool_extension(...)` desde el worker propio + reusar el `tool_extensions` registry |
| 4 | `import litellm` dentro de un `workflows/*.py` | Viola R-DET (workflows determinísticos) — el LLM es I/O | Mover el call a `activities/llm.py` y `workflow.execute_activity(llm_chat, ...)` |
| 5 | Crear `@dataclass` sin `frozen=True` que cruza workflow↔activity | Viola R-JSON — falla `test_r_json.py` (AST) | `@dataclass(frozen=True)` siempre que el DTO cruce boundary. Solo tipos JSON: `str`, `int`, `float`, `bool`, `None`, `list`, `dict`, otros dataclasses frozen |

Más anti-patterns + remedios en `references/deha-rules.md` y `references/fsd-rules.md`.

---

## §6. Snapshot del estado del repo (al momento de PR12)

| Dimensión | Valor |
|---|---|
| Plugins frontend | 5 (`chats`, `catalog`, `orders`, `eta`, `agents_admin`) |
| Plugins agentic | 2 (`chats` con 2 workers, `catalog` con 1 worker) |
| Workers Temporal totales | 3 (`chats/sales`, `chats/remarketing`, `catalog/sync`) |
| Routers FastAPI | 3 (todos de `chats`: sales / dashboard / handoff) |
| Tests Python | 293 passed |
| Tests architecture | 4 contratos R-DIP + invariantes R-JSON / R-HEARTBEAT |
| Tests plugins (invariants) | 6 (premortem) |
| Tests frontend (vitest) | 69 |
| Tests frontend architecture (dep-cruiser) | 4 rules |

Si estos números cambian sustancialmente al momento de leer este skill,
puede que la guía esté desactualizada — verificá `ARCHITECTURE.md §13`
(historia) para ver si hubo PRs nuevos.

---

## §7. Lo que NO va en este skill (y dónde está)

| Si buscás… | NO está acá. Está en… |
|---|---|
| Detalles de cómo correr el pipeline Archon (`archon workflow run …`) | `.archon/workflows/README-hubara.md` |
| Historia del refactor (PR0 → PR11 cronología) | `PLUGIN_REFACTOR_LOG.md` |
| Decisiones rechazadas + por qué (AgentSpan, LangGraph, etc.) | `PLUGIN_ARCHITECTURE.md §11` |
| Plan del pipeline hubara mismo (este skill es parte de ese plan) | `HUBARA_PIPELINE_PLAN.md` |
| Comandos `git` específicos | los emiten los workflows Archon, no este skill |
| Documentación de Temporal / FastAPI / React / Zod / etc. | sus docs oficiales — este skill cita el patrón en uso, no el API |

---

**Fin del SKILL.md.** Próximo paso recomendado para cualquier skill
downstream: leer `sections/01-general.md` para tener el mapa mental
completo antes de cargar las secciones específicas de tu tarea.
