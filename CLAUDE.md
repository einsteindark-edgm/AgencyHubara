# AgencyHubara — contexto para agente

> Archivo lean. Solo punteros y gotchas que ya nos quemaron.
> Detalle por subdirectorio: `hubara_agency/CLAUDE.md`, `frontend_dashboard/CLAUDE.md`.

## ¿Qué es esto?

Monorepo con backend Python (Temporal + DEHA hexagonal architecture) + frontend React/TS (Feature-Sliced Design) + plugin system (post-PR11). El operador implementa HUs end-to-end vía un pipeline Archon multi-agente.

## Mapa rápido

- `hubara_agency/` — backend Python. Workflows Temporal + Honest Agents. Detalle en `hubara_agency/CLAUDE.md`.
- `frontend_dashboard/` — frontend React/TS dashboard. FSD strict. Detalle en `frontend_dashboard/CLAUDE.md`.
- `exoclaw-temporal/` — librería base (DEHA reference). Plugin del pipeline; rara vez se modifica.
- `agent_coordination/` — utilidades cross-worker.
- `system_explorer/` — auditoría del repo.
- `.archon/workflows/` — pipeline definitions. **PROTECTED** (ver `hubara_agency/.hubara/spinal-files.yaml`).
- `.claude/skills/` — skills del pipeline (`hubara-*-archon`). **PROTECTED**.
- `.codegraph/` — knowledge graph del codebase. Usá `codegraph_*` tools antes de `grep`.
- `hubara_agency/.hubara/` — convenciones del pipeline (`project-context.md`, `spinal-files.yaml`).
- `CODEMAP.md` — mapa de navegación detallado.
- `HARNESS_UPGRADE_PLAN.md` — plan de mejora del propio pipeline.

## Para trabajar en una HU

- `archon workflow run hu-hubara-pipeline <issue-url>` arranca el pipeline.
- NUNCA editar `protected: true` paths de `hubara_agency/.hubara/spinal-files.yaml` sin ADR.
- Test/lint comandos por subdirectorio (ver `hubara_agency/.hubara/project-context.md` §2).
- Los skills `hubara-*-archon` se invocan desde Archon, NO directamente.

## Gotchas críticos (los que ya nos quemaron)

1. **Verificar comportamiento, no solo schema.** HUs de visualización requieren chequear que el backend EMITE los datos, no solo que el schema los permita. Caso paradigmático: HU mensajes-agente — tests verdes, feature rota.
2. **HU_ID inmutable desde iteración 1.** El planner skill cambiar de `hu_id` entre iteraciones causa directorios huérfanos en worktrees. Fijar id desde la primera iteración.
3. **`cd hubara_agency &&`** antes de cualquier `uv run`. **`cd frontend_dashboard &&`** antes de cualquier `npm` / `npx` / `tsc`. La pipeline tiene hooks pre-bash que lo enforzan automáticamente (Fase 1 del plan).
4. **Código vivo > docs grandes.** `ARCHITECTURE.md` (60 KB) y `HUBARA_PIPELINE_*.md` son históricos. Cuando una HU contradice un doc grande, el código vivo y `hubara-architecture-guide` son la fuente canónica.
5. **Codegraph stale.** Si `codegraph_*` devuelve resultados que no matchean el código vivo, gana el código vivo. Re-correr `codegraph_status` si parece desactualizado.

## Hooks activos (`.claude/settings.json`)

| Hook | Cuando | Qué hace |
|---|---|---|
| `pre-bash-cd-check.sh` | PreToolUse:Bash | Bloquea `uv run …` sin `cd hubara_agency &&`, idem `npm/npx/tsc` sin `cd frontend_dashboard &&` |
| `post-edit-lint.sh` | PostToolUse:Edit/Write | Corre `ruff check --fix` en `.py`, `eslint --fix` en `.ts/.tsx` |
| `post-edit-affected-tests-backend.sh` | PostToolUse:Edit/Write | **OPT-IN** (`CLAUDE_AFFECTED_TESTS=1`). Corre pytest del test heurístico + imports inversos |
| `post-edit-affected-tests-frontend.sh` | PostToolUse:Edit/Write | **OPT-IN** (`CLAUDE_AFFECTED_TESTS=1`). Corre `vitest related <file>` |
| `post-tool-log.sh` | PostToolUse:* | Appendea cada tool call a `hubara_agency/.hubara/agent-logs/<session>.jsonl` (observabilidad) |
| `stop-session-log.sh` | Stop | Persiste session metadata a `hubara_agency/.hubara/sessions/<id>.json` |
| `stop-handoff.sh` | Stop | Si sesión termina con trabajo pendiente en branch `hu/*`, escribe `handoff.yaml` para resume |
| `stop-arch-gate.sh` | Stop | Si tocó plugins/platform/shared, corre `lint-imports` + `pytest -m architecture` + `npm run test:arch` |

Para activar tests afectados en cada edit: `export CLAUDE_AFFECTED_TESTS=1` antes de la sesión.

## Tooling del pipeline (en `hubara_agency/.hubara/`)

| Script / archivo | Función |
|---|---|
| `smoke-test.sh` | Bearings smoke test E2E (git state + import backend + tsc frontend + dev servers detect). Usado por implementer §0.5 |
| `metrics-aggregator.sh` | Agrega session logs + tool logs + evaluation scores en `metrics.jsonl` por HU |
| `append-progress.sh` | Helper para que skills appendéen al progress log narrativo por HU (`progress-log/<HU_ID>.md`) |
| `evaluator-rubric.yaml` | Rúbrica graduable que consume `hubara-evaluator-archon` |
| `stress-test-protocol.md` | Ritual trimestral de re-evaluación del harness (qué componentes son load-bearing) |
| `evaluator-calibration/` | Corpus de PRs históricos para tunear el evaluator (templates + README) |
| `sessions/` | JSON por session — auto-escrito por `stop-session-log.sh` |
| `handoffs/` | YAML por sesión interrumpida — auto-escrito por `stop-handoff.sh` |
| `agent-logs/` | JSONL por session — auto-escrito por `post-tool-log.sh` |
| `progress-log/` | Markdown narrativo por HU — appendeado por `append-progress.sh` |

## Primitives del pipeline: `command:` vs `skills:` + `loop:`

Archon soporta 2 patrones de invocación. Cada uno tiene su lugar:

| Primitiva | Cuándo usarla | Archivo | Soporta `loop`/`gate_message` |
|---|---|---|---|
| **`command: <name>`** | One-shot ejecución, no necesita iteración | `.archon/commands/<name>.md` | ❌ |
| **`skills: [name]` + `loop:`** | Iteración interactiva (gate_message, max_iter > 1) | `.claude/skills/<name>/SKILL.md` | ✅ |

**Regla práctica:** si `max_iterations: 1` y no hay `gate_message`, usá `command:`. Si necesitás iterar con feedback humano, mantenete con `skills:` + `loop:`.

**Cobertura actual del pipeline hu-hubara-pipeline.yaml:**

**Commands** (9 nodos en `.archon/commands/`):
- `hubara-merge-intents` (consolidación spinal files multi-plugin)
- `hubara-premortem` (10 categorías de modos de fallo)
- `hubara-evaluate` (rúbrica calibrada 5 criterios)
- `hubara-reviewer-{deha,fsd,plugin-system,test-coverage,security}` (5 specialists paralelos)
- `hubara-synthesize-review` (consolida los 5 outputs)

**Skills + loop** (4 nodos, interactivos):
- `hubara-tech-refiner-archon` (max_iter:2, gate_message — operador puede pedir ajustes)
- `hubara-plugin-planner-archon` (max_iter:2, gate_message)
- `hubara-implementer-archon` (en loop-implementer-resolves-premortem + loop-implementer-resolves-review, max_iter:2)

**Skills sin invocación directa por workflow** (templates / referencias):
- `hubara-feature-planner-archon` (invocado por `hu-hubara-plugin-pipeline.yaml` con gate_message)
- `hubara-explorer-archon` (template para `Agent(subagent_type='Explore')`)
- `hubara-architecture-guide` (referencia leída via `Read` desde otros skills/commands)

## Pipeline post-implementer (3 gates pre-PR + loops al implementer)

Después de que el implementer termina cada task, el pipeline corre 3 gates secuenciales antes de abrir el PR. Cada gate que encuentra issues **vuelve al implementer** con la lista (`$LOOP_USER_INPUT`) — el detector NO aplica fixes (evita deuda silenciosa).

```
final-validation (tests + arch gates + tsc + build)
   ↓
[Gate 1] premortem-self-review (T6/T7 — divergent forward-looking)
   ↓  hubara-premortem-archon imagina 30-50 modos de fallo en 10 categorías
   ↓  emite failure_modes[] en premortem.yaml
   ├─ clean → al evaluator
   └─ has_issues → loop-implementer (§2.5, max 2 iter)
                    ├─ resolved → al evaluator
                    └─ blocked → cancel-on-premortem-blocked
   ↓
[Gate 2] evaluate-pre-pr (T7 — convergent rubric scoring)
   ↓  hubara-evaluator-archon score contra rúbrica calibrada
   ↓  emite evaluation.yaml con verdict pass/warn/block_merge
   ├─ pass/warn → al code review
   └─ block_merge → cancel-on-eval-block
   ↓
[Gate 3] multi-agent-code-review (T2/T7 — defensa en profundidad multi-agent)
   ↓  hubara-code-review-archon spawna 5 specialists en paralelo:
   ↓    DEHA · FSD · plugin-system · test-coverage · security
   ↓  cross-ref con premortem.yaml para NO duplicar
   ↓  emite code-review-findings.yaml
   ├─ clean → trigger-pr
   └─ has_issues → loop-implementer (§2.6, max 2 iter)
                    ├─ resolved → trigger-pr
                    └─ blocked → cancel-on-review-blocked
   ↓
PR
```

**Tres lentes complementarias:**
- **Premortem** (forward-looking): ¿qué podría salir mal en producción? Edge cases, race conditions, network failures, i18n, observability, performance, UI states.
- **Evaluator** (convergent rubric): score numérico contra 5 criterios pre-acordados (architectural, test coverage real, visual, code quality, scope).
- **Code review** (multi-agent specialists): 5 expertos paralelos auditan SU área vertical (R-rules, FSD anti-patterns, plugin schema, behavior tests, security).

**Diseño deliberado:** ningún gate aplica fixes. Cada uno emite findings + suggested_fix + complexity, y delega al implementer (vía `$LOOP_USER_INPUT`) — que aplica con su contexto completo (exploration map, sibling patterns, R-rules). Si los issues son `complex` (signature change / refactor / requieren ADR), el implementer los marca `deferred` y el workflow se cancela visiblemente. **No hay deuda silenciosa.**

## Para detalles canónicos

- DEHA + R-rules + FSD + plugin system: `.claude/skills/hubara-architecture-guide/sections/`
- Convenciones pipeline: `hubara_agency/.hubara/project-context.md`
- Spinal files (paths cross-plugin / protected): `hubara_agency/.hubara/spinal-files.yaml`
- Plan de mejora del harness: `HARNESS_UPGRADE_PLAN.md`
- Concepto base de harness engineering: `HARNESS_ENGINEERING.md`
