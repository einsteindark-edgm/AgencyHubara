# AgencyHubara — mapa de navegación

> Tabla de contenidos para que el agente sepa dónde buscar antes de abrir archivos.
> Una línea por entry. Si una sección se desactualiza, gana el filesystem.

## Top-level (`/`)

| Dir / file | Función | Cuándo entrar |
|---|---|---|
| `hubara_agency/` | Backend Python (Temporal + DEHA + plugins) | Cualquier HU que toque agentes, workflows, activities, FastAPI |
| `frontend_dashboard/` | Frontend React/TS dashboard (FSD strict) | Cualquier HU que toque UI |
| `exoclaw-temporal/` | Librería base de patrones Temporal (DEHA reference) | Rara vez. Solo si la HU pide cambiar el framework base |
| `agent_coordination/` | Utilidades cross-worker (orchestration helpers) | HU multi-worker con coordinación |
| `system_explorer/` | Tool de auditoría del repo | Debug / health-check del repo |
| `features/` | Tareas HU en curso (work-in-progress, ignored por .gitignore) | NO entrar como referencia — son artefactos |
| `.archon/workflows/` | Pipeline definitions YAML | **PROTECTED**. Solo modificar vía ADR |
| `.claude/skills/` | Skills del pipeline (`hubara-*-archon` + `hubara-architecture-guide`) | **PROTECTED**. Solo modificar vía ADR del harness |
| `.codegraph/` | SQLite knowledge graph del codebase | Consultar vía `codegraph_*` tools |
| `.cursor/` | Cursor IDE state | Ignorar |

## Docs en root (rara vez relevantes durante implementación)

| File | Tamaño | Estado | Cuándo leer |
|---|---|---|---|
| `CLAUDE.md` | lean | **vivo** | Cada sesión, autocargado |
| `CODEMAP.md` | este file | **vivo** | Cuando no sabés dónde está algo |
| `HARNESS_ENGINEERING.md` | 62 KB | **vivo** | Manual de harness eng (1x lectura) |
| `HARNESS_UPGRADE_PLAN.md` | 36 KB | **vivo** | Plan de mejora del pipeline |
| `ARCHITECTURE.md` | 60 KB | **histórico** | Solo si nada más responde la pregunta — superseded por `hubara-architecture-guide` |
| `HUBARA_PIPELINE_GUIDE.md` | 50 KB | histórico | Idem |
| `HUBARA_PIPELINE_PLAN.md` | 53 KB | histórico | Idem |
| `HUBARA_PIPELINE_INVENTORY.md` | 13 KB | histórico | Idem |
| `HUBARA_SKILL_BLUEPRINT.md` | 29 KB | histórico | Idem |
| `HUBARA_WORKFLOWS_BLUEPRINT.md` | 52 KB | histórico | Idem |
| `PLUGIN_ARCHITECTURE.md` | 27 KB | histórico | Idem |
| `PLUGIN_REFACTOR_LOG.md` | 70 KB | histórico | Idem |
| `PLUGIN_REFACTOR_PLAN.md` | 46 KB | histórico | Idem |
| `ADR-2026-05-19-string-based-workflow-dispatch.md` | 30 KB | **vivo** | Si la HU toca cross-worker orchestration |
| `ADR-2026-05-20-declarative-orchestration.md` | 27 KB | **vivo** | Idem |
| `cheatsheet_produccion.md` | 5 KB | **vivo** | Deploy / k8s ops |

> **Regla:** cuando un doc histórico contradice el código vivo o `hubara-architecture-guide`, el código y el guide ganan.

## `hubara_agency/` (backend)

| Path | Función |
|---|---|
| `src/platform/` | Cross-plugin: contracts, registries, composition, tool extensions, constants |
| `src/platform/contracts.py` | DTOs frozen compartidos (**spinal**, protected) |
| `src/platform/registries.py` | Plugin factory registries (**spinal**, protected) |
| `src/platform/tool_extensions.py` | Tool extension registry (**spinal**, protected) |
| `src/platform/constants.py` | Constants cross-plugin (**spinal**, protected) |
| `src/platform/plugin_manifest.py` | Loader del `plugin.yaml` |
| `src/plugins/<id>/` | Plugin (bounded context): `agent/`, `workers/`, `api/` |
| `src/plugins/<id>/agent/composition.py` | Factories `@lru_cache(maxsize=1)` |
| `src/plugins/<id>/agent/workspace/` | Markdown del agent: IDENTITY, TOOLS, PROMPTS |
| `src/plugins/<id>/agent/tools/` | Tools LLM (snake_case) |
| `src/plugins/<id>/workers/` | Temporal workers (workflows + activities) |
| `src/plugins/<id>/api/` | Endpoints FastAPI (opcional, solo algunos plugins) |
| `tests/architecture/` | Gates DEHA (R-rules) — **PROTECTED** |
| `tests/plugins/test_premortem_invariants.py` | Plugin system invariants — **PROTECTED** |
| `tests/functional/` | E2E backend (`@pytest.mark.functional`) |
| `tests/plugins/<id>/` | Unit tests por plugin |
| `.hubara/project-context.md` | Convenciones canónicas pipeline |
| `.hubara/spinal-files.yaml` | Spinal files + protected paths |
| `.importlinter` | R-DIP enforcement config |
| `k8s/aws-produccion/` | Deployment manifests por worker |
| `scripts/render-compose.py` | Codegen `docker-compose.local.yml` |
| `hubara_vault/` | Runtime state (sessions, catalog) — seed data committeado |
| `run_api.py`, `src/run_workers.py`, `src/main.py` | Entry points |

**Plugins backend actuales:** `agents_admin`, `catalog`, `chats` (2 workers: sales + remarketing), `eta`, `orders`, `system_map`.

## `frontend_dashboard/` (frontend)

| Path | Función |
|---|---|
| `src/shared/` | UI primitives + lib + api + config |
| `src/shared/ui/Icon.tsx` | iconRegistry append-only — **spinal** |
| `src/shared/{ui,lib,api,config}/index.ts` | Barrels — **spinal** |
| `src/entities/<id>/` | Entity layer: `model.ts`, `api.ts`, `contracts.ts` (Zod), `keys.ts`, `index.ts` |
| `src/features/` | Componentes con lógica UX/business |
| `src/plugins/<id>/frontend/` | Plugin-local FSD (mini árbol con pages/features/entities) |
| `src/plugins/_schema/` | Codegen artifact — NO editar |
| `src/pages/` | Composición page-level |
| `src/app/providers/index.tsx` | Composition root — **spinal** |
| `src/app/router.tsx` | Routing |
| `src/test/architecture/` | Dep-cruiser tests — **PROTECTED** |
| `src/index.css` | `@theme` Tailwind v4 + globals (93 KB, mayoría `@theme`) |
| `e2e/<feature>/` | Playwright specs |
| `scripts/plugins-sync.ts` | Plugin codegen runner |
| `.dependency-cruiser.cjs` | Arch rules — **PROTECTED** |
| `tsconfig.arch.json` | Arch typecheck — **PROTECTED** |
| `playwright.config.ts` | E2E config |
| `vite.config.ts`, `vitest.config.ts` | Build / test config |

**Plugins frontend actuales:** `ads`, `agents_admin`, `catalog`, `chats`, `eta`, `orders`, `system_map`.

## Pipeline (`.archon/` + `.claude/skills/`)

| Path | Función |
|---|---|
| `.archon/workflows/idea-a-hu-hubara.yaml` | Idea → HU formal → Issue + Project card |
| `.archon/workflows/hu-hubara-pipeline.yaml` | Pipeline principal: refiner → planner → implementer → merger → PR |
| `.archon/workflows/hu-hubara-plugin-pipeline.yaml` | Sub-pipeline para 1 plugin (invocado en fan-out multi-plugin) |
| `.archon/workflows/review-pr-hubara.yaml` | Review post-merge (5 agentes especializados) |
| `.claude/skills/hubara-tech-refiner-archon/` | HU cruda → `hu-refinada.md` |
| `.claude/skills/hubara-plugin-planner-archon/` | refinement → `plugin-manifest.yaml` (DAG plugin-level) |
| `.claude/skills/hubara-feature-planner-archon/` | plugin slice → `feature-plan-manifest.yaml` + `tareas/F<NN>` |
| `.claude/skills/hubara-implementer-archon/` | task → código + tests + `task-result.yaml` |
| `.claude/skills/hubara-merger-archon/` | N `task-result.yaml` → spinal files mergeados |
| `.claude/skills/hubara-architecture-guide/` | Guía arquitectural en capas (10 secciones + 4 references) |

## Codegraph (consultá ANTES de grep / find)

| Tool | Para |
|---|---|
| `codegraph_search <name>` | "¿Dónde está el símbolo X?" |
| `codegraph_context <symbol>` | "Dame contexto sobre X" (combina search + node + callers/callees) |
| `codegraph_callers <symbol>` | "¿Qué llama a X?" |
| `codegraph_callees <symbol>` | "¿Qué llama X?" |
| `codegraph_impact <symbol>` | "¿Qué se rompería si cambio X?" — **OBLIGATORIO antes de modificar signatures** |
| `codegraph_node <id>` | Source / signature / docstring |
| `codegraph_explore <symbols>` | Source de varios símbolos relacionados de una |
| `codegraph_files <path>` | "¿Qué hay bajo path X?" |
| `codegraph_status` | "¿El index está fresco?" |

**Regla del §17.3 del HARNESS_ENGINEERING.md:** cuando codegraph y el código vivo discrepan, gana el código vivo.
