# Pipeline Hubara — Guía operacional

Pipeline Archon end-to-end para HUs de AgencyHubara (DEHA backend + FSD
frontend + plugin system post-PR11). 3 workflows + 1 review + 6 skills
+ 1 skill arquitectural unificado, todos diseñados para paralelizar la
implementación a nivel **plugin** (la unidad ortogonal natural del repo).

> **Documentos relacionados:**
> - `HUBARA_PIPELINE_PLAN.md` — el plan maestro con decisiones de diseño.
> - `HUBARA_SKILL_BLUEPRINT.md` — spec de los skills.
> - `HUBARA_WORKFLOWS_BLUEPRINT.md` — pseudocódigo de los workflows.
> - `.archon/workflows/README.md` — pipeline `exoclaw` legacy.
> - `.archon/workflows/README-frontend.md` — pipeline `frontend` legacy.

---

## §1. Componentes

### §1.1 Skills (`.claude/skills/`)

| Skill | Rol | Escribe código? |
|---|---|---|
| `hubara-architecture-guide` | Single source of truth de la arquitectura (10 sections + 4 refs + 4 examples, ~320 KB modular) | No |
| `hubara-tech-refiner-archon` | Refina HU cruda → tech refinement con 14 secciones + §0 Plugin Classification | No |
| `hubara-plugin-planner-archon` | Decompone refinement → plugin-manifest.yaml (DAG plugin-level + batches) | No |
| `hubara-feature-planner-archon` | Dentro de UN plugin: decompone work_summary → feature-plan-manifest.yaml + tareas/F<NN>-*.md | No |
| `hubara-implementer-archon` | Implementa UNA task F<NN>: edita Python + TS, corre gates, escribe task-result.yaml con wiring_intents | **Sí** |
| `hubara-merger-archon` | Consolida wiring_intents de N sub-pipelines paralelos en spinal files | **Sí** (solo spinal) |

**Patrón clave:** los 5 skills `*-archon` cargan SOLO las secciones
relevantes del `hubara-architecture-guide` via `Read` tool. Context window
controlado (~30-50 KB por skill por task, no las 320 KB completas).

### §1.2 Workflows (`.archon/workflows/`)

| Workflow | Comando | Rol |
|---|---|---|
| `idea-a-hu-hubara` | `archon workflow run idea-a-hu-hubara "<idea>"` | Entry-point: idea cruda → HU narrativa → Issue + Project card → approval para lanzar pipeline |
| `hu-hubara-pipeline` | `archon workflow run hu-hubara-pipeline "<issue-url-or-HU-id>"` | Super-orquestador E2E: refinar → plan plugin-level → impl (single inline / multi fan-out) → valid final → PR → trigger review |
| `hu-hubara-plugin-pipeline` | `archon workflow run hu-hubara-plugin-pipeline "<HU_ID> <plugin_id>"` | Sub-pipeline por plugin: feature plan + implementar secuencial con gates determinísticos |
| `review-pr-hubara` | `archon workflow run review-pr-hubara "<PR_URL>"` | Review automático: 5 agentes inline (deha + fsd + plugin-system + test-coverage + security) + auto-fix CRITICAL/HIGH + comment al PR |

### §1.3 Convenciones (`hubara_agency/.hubara/`)

| Archivo | Quién lo escribe | Quién lo lee |
|---|---|---|
| `spinal-files.yaml` | Operador (1 vez, manual) | Planners, implementer, merger |
| `project-context.md` | Operador (1 vez, manual) | TODOS los skills (lo leen primero en Step 0) |
| `refinements/<HU_ID>-tech.md` | `hubara-tech-refiner-archon` (vía `hu-hubara-pipeline` FASE 1) | `hubara-plugin-planner-archon`, downstream skills |
| `refinements/<HU_ID>-original.md` | `hu-hubara-pipeline` (cp del input) | fallback context |
| `plans/<HU_ID>/plugin-manifest.yaml` | `hubara-plugin-planner-archon` (vía FASE 2) | `hu-hubara-plugin-pipeline`, orquestador |
| `plans/<HU_ID>/feature-plans/<plugin>/feature-plan-manifest.yaml` | `hubara-feature-planner-archon` (vía sub-pipeline FASE 1) | `hubara-implementer-archon`, sub-pipeline |
| `plans/<HU_ID>/feature-plans/<plugin>/tareas/F<NN>-*.md` | Mismo skill | Mismo |
| `results/<HU_ID>/plugin-<plugin_id>-result.yaml` | `hu-hubara-plugin-pipeline` FASE 3 | Orquestador (validación batch + merger) |
| `results/<HU_ID>/feature-results/<plugin>/F<NN>-result.yaml` | `hubara-implementer-archon` (vía until_bash del sub-pipeline) | Sub-pipeline + orquestador (vía plugin-result agregado) |

---

## §2. Modelo operacional (dos niveles)

```
NIVEL A — plugin-level (en hu-hubara-pipeline)
  └── DAG de plugins. Cada nodo = "trabajo a hacer en UN plugin".
      Plugins ortogonales corren en PARALELO (fan-out manual de N terminales).
      Plugins con deps → topological order (batches secuenciales).

NIVEL B — feature-level (en hu-hubara-plugin-pipeline, dentro de cada plugin)
  └── DAG de features. Cada tarea = vertical slice (DTO + tool + tests).
      DEFAULT secuencial (intra-plugin las features comparten worker.py,
      composition.py, etc. — paralelizar tiene poco sentido).
```

**Mode classification** (decidido por `hubara-tech-refiner-archon §0`):

- **`single_plugin`** (mayoría): 1 plugin afectado. Orquestador corre el
  sub-pipeline INLINE (sin fan-out, sin gates humanos).
- **`multi_plugin`**: 2+ plugins afectados. Orquestador imprime N
  comandos de fan-out, espera approval ("ready"), valida batch, opcional
  invoca merger.
- **`no_work`** (HU short-form): refinement no aplica. Pipeline termina temprano.

---

## §3. Modelo de paths (`$ARTIFACTS_DIR` vs `hubara_agency/.hubara/`)

```
┌──────────────────────────────────────────────────────────────────────┐
│ $ARTIFACTS_DIR                                                        │
│   Path: ~/.archon/workspaces/<owner>/<repo>/artifacts/runs/<run-id>/ │
│   Vida: SOLO durante UN run de workflow                               │
│   Uso: los skills (genéricos) leen/escriben con nombres canónicos     │
│        (hu-refinada.md, plugin-manifest.yaml, plugin-work.yaml,       │
│        task.md, task-result.yaml, feature-plan-manifest.yaml, etc.)   │
│   NO commiteado, NO persistido                                        │
└──────────────────────────────────────────────────────────────────────┘
                                ↑
                                │ cp por nodos cargar-* / persistir-*
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│ hubara_agency/.hubara/                                                │
│   Path: <worktree-root>/hubara_agency/.hubara/                        │
│   Vida: hasta que el operador la borre del repo                       │
│   Uso: workflows (que saben el HU_ID) persisten outputs para el      │
│        próximo workflow                                               │
│   SE commitea, SE mergea a main para cross-workflow handoff           │
└──────────────────────────────────────────────────────────────────────┘
```

**Regla:** los skills nunca leen ni escriben directo en
`hubara_agency/.hubara/` — los nodos `cargar-*` y `persistir-*` /
`commit-*` del workflow hacen el bridge.

---

## §4. Timeline E2E de una HU (single_plugin auto)

```
OP = operador                                ARCHON = la CLI

┌────────────────────────────────────────────────────────────────────────┐
│ FASE 0 — Idea → Issue                                                   │
├────────────────────────────────────────────────────────────────────────┤
│ OP    │ archon workflow run idea-a-hu-hubara "agregar tool send_image"  │
│ ARCHON│ refina la idea en HU formal (1 pasada AI)                       │
│       │ → publica Issue en GitHub (label: hubara-hu)                    │
│       │ → agrega al Project board (status: Idea refined)                │
│       │ → approval gate                                                  │
│ OP    │ aprobá → dispara hu-hubara-pipeline en background               │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 1 — Refinar técnico                                                │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ FASE 0: check-prereqs + crea branch hu/<HU_ID> + push           │
│       │ FASE 1: invoca hubara-tech-refiner-archon                       │
│ SKILL │ carga guide (sections/01 + 07 + por layers) + hu-original       │
│       │ produce hu-refinada.md con 14 secciones + §0 Plugin             │
│       │ classification (single_plugin)                                  │
│ ARCHON│ commit refinement → hubara_agency/.hubara/refinements/         │
│       │ Project: "Refined"                                              │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 2 — Plan plugin-level                                              │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ invoca hubara-plugin-planner-archon                             │
│ SKILL │ lee hu-refinada §0 → produce plugin-manifest.yaml               │
│       │ con plugins:[{id:chats, layers:[agent], ...}] + batches:[B1]    │
│ ARCHON│ commit plan → hubara_agency/.hubara/plans/<HU_ID>/             │
│       │ Project: "Planned"                                              │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 3 — Implementación (rama A: single_plugin inline)                  │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ classify-mode detecta mode=single_plugin                        │
│       │ rama-A invoca hu-hubara-plugin-pipeline "<HU_ID> chats" inline │
│       │ (env -u CLAUDECODE, espera resultado)                           │
│       │ ── sub-pipeline ──                                              │
│       │   ARCHON: checkout hu/<HU_ID> + stage plugin-work.yaml         │
│       │   SKILL hubara-feature-planner-archon:                          │
│       │     lee plugin-work → produce feature-plan + tareas/F01-*.md   │
│       │   ARCHON: commit feature-plan + push                            │
│       │   LOOP implementar-secuencial (until_bash determinista):        │
│       │     SKILL hubara-implementer-archon:                            │
│       │       - lee task.md F01                                         │
│       │       - carga guide selectivo (sections/02-04 si backend)       │
│       │       - escribe código en src/plugins/chats/                    │
│       │       - corre §10 commands                                      │
│       │       - escribe task-result.yaml con status=passed + intents   │
│       │     UNTIL_BASH:                                                 │
│       │       - re-corre TODOS los gates determinísticamente:           │
│       │         uv pytest + pytest -m architecture + lint-imports +    │
│       │         render-compose drift + npm test + test:arch + tsc +    │
│       │         build + playwright (si tocó UI)                         │
│       │       - si gate falla con AI passed → det-retry (max 2)         │
│       │       - si pasa: commit + push a hu/<HU_ID> + next task        │
│       │   FASE 3 sub-pipeline: write plugin-result.yaml + push          │
│       │ ── back to orchestrator ──                                      │
│       │ rama-A: git fetch + ff-merge + valida plugin-result=passed     │
│       │ Project: "Implementing" → mantenido hasta FASE 5                │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 4 — Validación final consolidada                                   │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ corre TODOS los gates consolidados:                             │
│       │   - render-compose drift                                        │
│       │   - uv pytest full + -m architecture + tests/plugins/          │
│       │   - uv pytest tests/functional/ -m functional -v               │
│       │   - uv lint-imports                                             │
│       │   - npm test + test:arch                                        │
│       │   - npx tsc -b + npm run build                                  │
│       │   - Playwright E2E con FastAPI background (random port)         │
│       │ Si CUALQUIERA falla → cancel con diagnose commands              │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 5 — PR                                                              │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ build-pr-body (script bun): consolida summary HU + plugins +    │
│       │   results + functional evidence + playwright evidence inline    │
│       │ gh pr create --body-file → PR creado                            │
│       │ Project: "Done — PR ready"                                      │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 6 — Trigger review (background)                                    │
├────────────────────────────────────────────────────────────────────────┤
│ ARCHON│ env -u CLAUDECODE nohup archon workflow run review-pr-hubara    │
│       │   "<PR_URL>" & disown                                           │
│       │ ── review-pr-hubara (background, ~3-5 min) ──                   │
│       │   fetch PR + checkout branch + fetch diff                       │
│       │   classify (haiku) decide qué de los 5 agentes correr          │
│       │   5 agentes en paralelo (deha/fsd/plugin-system/test-cov/sec)  │
│       │   synthesize → review-report.md + auto-fix-plan.yaml            │
│       │   auto-fix CRITICAL/HIGH (revierte si rompe tests)              │
│       │   commit fixes + push                                           │
│       │   post comment al PR                                            │
│       │   Project: "Reviewing"                                          │
└────────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────────┐
│ FASE 7 — Operador                                                       │
├────────────────────────────────────────────────────────────────────────┤
│ OP    │ revisa PR + comment del review                                  │
│       │ squash-merge a main                                             │
│       │ Issue se cierra automático (Closes <url> en PR body)            │
└────────────────────────────────────────────────────────────────────────┘
```

**Estimado total:** ~20-40 min para HU single-plugin típica (~3-6 tasks).

---

## §5. Timeline E2E de una HU (multi_plugin con fan-out)

Diferencias respecto del flujo single_plugin:

```
FASE 3 — rama B (multi-plugin fan-out):

  ARCHON│ classify-mode detecta mode=multi_plugin
        │ rama-B-print-fan-out-commands:
        │   IMPRIME N comandos copy-pasteable, uno por plugin del primer batch:
        │     archon workflow run hu-hubara-plugin-pipeline "<HU_ID> chats"
        │     archon workflow run hu-hubara-plugin-pipeline "<HU_ID> catalog"
        │   approval gate → "ready"

  OP    │ Abre N terminales nuevas, corre cada comando.
        │ Cada sub-pipeline:
        │   - crea worktree fresh desde origin/hu/<HU_ID>
        │   - feature plan + implementar secuencial
        │   - commit + push a hu/<HU_ID> con pull-rebase retry
        │ Todos commitean al MISMO branch hu/<HU_ID>.

  OP    │ Cuando TODOS sub-pipelines terminaron, vuelve a la terminal
        │ del orquestador y responde "ready" al approval.

  ARCHON│ rama-B-merge-batch: git fetch + ff-merge origin/hu/<HU_ID>
        │ Valida plugin-*-result.yaml status=passed para cada plugin del batch.
        │ Si alguno failed → cancel con detalle.
        │
        │ Si requires_merger (set por plugin-manifest):
        │   rama-B-invoke-merger-if-shared:
        │     SKILL hubara-merger-archon:
        │       - lee plugin-results + spinal-files.yaml
        │       - aggregate wiring_intents por spinal_path
        │       - apply intents deterministically, validate sintaxis
        │       - emite merge-report.yaml
        │   rama-B-commit-merger: commit spinal files consolidados + push

  ARCHON│ FASE 4 sigue igual (validación final consolidada)
```

**Estimado total:** ~30-60 min para HU multi-plugin típica (~2-4 plugins,
fan-out paralelo reduce wall time).

---

## §6. Manual del operador

### §6.1 Setup inicial (1 vez por repo)

Todo debe estar **committeado en main** antes de la primera corrida —
Archon crea worktrees fresh desde `origin/main`.

```bash
# 1. Convenciones (PR13)
ls hubara_agency/.hubara/
#   → spinal-files.yaml                       ✓ obligatorio
#   → project-context.md                      ✓ obligatorio

# 2. Skill arquitectural (PR12)
ls .claude/skills/hubara-architecture-guide/
#   → SKILL.md, README.md, sections/01..10, references/, examples/

# 3. 5 skills del pipeline (PR14, 15, 16, 18)
ls .claude/skills/ | grep hubara-.*-archon
#   → hubara-tech-refiner-archon              ✓ PR14
#   → hubara-plugin-planner-archon            ✓ PR15
#   → hubara-feature-planner-archon           ✓ PR16
#   → hubara-implementer-archon               ✓ PR16
#   → hubara-merger-archon                    ✓ PR18

# 4. 4 workflows (PR14, 15, 16, 17, 18)
ls .archon/workflows/ | grep hubara
#   → idea-a-hu-hubara.yaml                   ✓ PR14
#   → hu-hubara-pipeline.yaml                 ✓ PR15+17
#   → hu-hubara-plugin-pipeline.yaml          ✓ PR16
#   → review-pr-hubara.yaml                   ✓ PR18

# 5. Pre-requisitos de runtime
gh auth status                            # gh autenticado
gh auth refresh -s project,read:project   # Projects v2 scope
command -v node && command -v npm
command -v uv
command -v bun
command -v jq
command -v python3
command -v curl
cd hubara_agency && uv sync               # warm el venv
cd frontend_dashboard && npm ci           # warm node_modules

# 6. (Opcional) GitHub Project config
ls .archon/github-project-config.yaml     # opcional; status_options:
                                          #   - Idea refined
                                          #   - Refining / Refined
                                          #   - Planning / Planned
                                          #   - Implementing
                                          #   - Reviewing
                                          #   - Done — PR ready
                                          #   - Blocked
```

### §6.2 Por cada HU (flujo recomendado)

```bash
# Idea cruda → Issue → approval → pipeline en background
archon workflow run idea-a-hu-hubara "agregar tool de envío de imágenes al agente sales"

# (responde "aprobada" cuando AI te muestra el draft)
# (responde "sí" al approval gate de "lanzar pipeline")

# El pipeline corre en background. Mirá el Project board para ver progreso.

# Si single-plugin: NADA más que hacer hasta que termine el PR (~20-40 min).

# Si multi-plugin: en algún momento el orquestador te va a imprimir
# comandos de fan-out + approval "ready". Abrí N terminales, corré cada
# comando, esperá que terminen, volvé al orquestador, decí "ready".
```

### §6.3 Modos alternativos

**Modo B — Sub-pipeline standalone (debug / fix puntual):**

```bash
# Trabajar SOLO en un plugin específico de una HU existente:
archon workflow run hu-hubara-plugin-pipeline "<HU_ID> chats"
```

**Modo C — Smart resume:**

```bash
# Si pipeline falla, fixeás a mano + commit + push a hu/<HU_ID>:
archon workflow run hu-hubara-pipeline "<HU_ID>"
# Smart-resume salta FASE 1 (refinement OK) + FASE 2 (plan OK) + tasks
# ya pasadas, retoma donde quedó.
```

**Modo D — Override manual:**

```bash
# Editar refinement / plan / task file a mano:
$EDITOR hubara_agency/.hubara/refinements/<HU_ID>-tech.md
git add hubara_agency/.hubara/refinements/
git commit -m "<HU_ID>: refinement manual override"
git push origin hu/<HU_ID>
# Re-lanzar pipeline: smart-resume salta FASE 1 (refinement ya existe)
archon workflow run hu-hubara-pipeline "<HU_ID>"
```

---

## §7. Troubleshooting

| Síntoma | Causa probable | Acción |
|---|---|---|
| `check-prereqs FAIL_GH_AUTH` | gh no autenticado | `gh auth login` + `gh auth refresh -s project,read:project` |
| `check-prereqs FAIL_GH_NO_PROJECT_SCOPE` | scope project missing | `gh auth refresh -s project,read:project` |
| `check-prereqs FAIL_MISSING_*` | archivo no committeado en main | commit + push, después re-lanzar |
| `check-prereqs FAIL_DIRTY_PROTECTED_FILES` | tu main tiene cambios sin commit en `.archon/` o `.claude/skills/hubara-*` (Archon los copia al worktree) | commit / discardalos en main + re-lanzar |
| `validate-refinement FAIL_NO_PLUGIN_CLASSIFICATION` | el refiner no escribió §0 Plugin Classification | iterá el refiner con feedback "agregá §0" o editá a mano |
| `validate-refinement FAIL_REFINEMENT_TOUCHES_PROTECTED` | el refinement pide cambios architecture-protected | requiere ADR — PR separado con label architecture-change |
| `validate-plan FAIL_TOO_MANY_PLUGINS: N > cap=8` | el planner emitió >8 plugins (cap `MAX_PLUGINS_PER_HU=8` default) | (a) splittear en 2 HUs; (b) override sólo si el caso amerita: `MAX_PLUGINS_PER_HU=N archon workflow run hu-hubara-pipeline "<HU>"` |
| `validate-feature-plan FAIL_TOO_MANY_TASKS: N > cap=12` | feature-planner emitió >12 tasks (cap `MAX_FEATURES_PER_PLUGIN=12` default) | (a) splittear el plugin work en 2 HUs ortogonales; (b) override: `MAX_FEATURES_PER_PLUGIN=N archon workflow run hu-hubara-plugin-pipeline "<HU> <plugin>"` |
| `final-validation` saltea Playwright / npm / pytest | scope detection vs `origin/main` no detectó cambios en ese stack | esperado — ahorra ~3-7 min en HUs backend-only. Si querés forzar todos los gates: `FORCE_ALL_GATES=1 archon workflow run hu-hubara-pipeline "<HU>"` |
| `rama-B-merge-batch FAIL_BATCH_INCOMPLETE missing=N failed=M` | algún sub-pipeline no terminó o falló | revisar `hubara_agency/.hubara/results/<HU_ID>/`, re-lanzar los faltantes, responder "ready" otra vez |
| `final-validation FAIL_RENDER_COMPOSE_DRIFT` | algún sub-pipeline tocó manifest pero no commit el compose | `cd hubara_agency && uv run python scripts/render-compose.py && git add docker-compose.local.yml && git commit && git push` |
| `final-validation FAIL_LINT_IMPORTS` | violación R-DIP introducida | leer `lint-imports` output, fixear import path, re-lanzar |
| `final-validation FAIL_ARCH` | violación R-rules (R-JSON / R-HEARTBEAT / etc.) | leer `pytest -m architecture` output, fixear, re-lanzar |
| `final-validation FAIL_PLAYWRIGHT` | E2E falló contra FastAPI random port | revisar log; backend down? Selector flaky? agregar wait explícito |
| `create-pr FAIL_PR_CREATE` | gh pr create error (auth o ya existe) | `gh pr list --head hu/<HU_ID>` y check; si existe, re-utilizar (el nodo lo detecta) |
| sub-pipeline `FAIL_BRANCH_NOT_FOUND` | invocaste plugin-pipeline antes de que el orquestador cree el branch | corré primero `hu-hubara-pipeline "<HU>"` para crear hu/<HU_ID> en origin |
| `det-retries-${TASK_ID}.count` agotado (2/2) | task falla los gates determinísticos 2x | revisar test-failures.md, editá tarea o código a mano, re-lanzar |
| `until_bash` pierde commits del sub-pipeline | race entre rebase y push | retry automático con pull-rebase ya está; si persiste, fetch + merge manual al final |
| Review post-PR muy lento (>10 min) | mucho diff o agente colgado | revisar `~/.archon/logs/review-hubara-*.log`; cancelar y re-lanzar con `archon workflow run review-pr-hubara "<PR>"` manual |

---

## §8. Diferencias vs pipelines legacy

| Aspecto | exoclaw legacy | frontend legacy | hubara (este) |
|---|---|---|---|
| Lenguaje target | Python | TypeScript | Python + TS (fusionado) |
| Entry point con Issue | No | Sí | Sí (`idea-a-hu-hubara`) |
| Skill arquitectural | (cada skill duplica contenido) | (mismo) | UNO unificado, modular cargado por sección |
| Plan plugin-level | No existe | No existe | **Sí (`hubara-plugin-planner`)** |
| Plan feature-level | Sí (`exoclaw-task-planner`) | Sí (`frontend-task-planner`) | Sí (`hubara-feature-planner`, dentro de cada plugin) |
| Modo single-plugin auto | Manual fan-out | Auto secuencial | **Auto inline** |
| Modo multi-plugin paralelo | Manual fan-out | No existe | **Manual fan-out con merger automático** |
| Merger automático | Sí (`exoclaw-merger`) | No | **Sí (`hubara-merger`, on-demand)** |
| Gates determinísticos en `until_bash` | No | Sí | Sí (extendido con render-compose drift) |
| Render-compose check | n/a | n/a | **Sí** (necesario post-PR11) |
| Playwright E2E con FastAPI random port | No | Sí | Sí (heredado) |
| Code review automático | No | Sí | **Sí** (`review-pr-hubara` con 5 agentes) |
| Cuántos PRs por HU | varía | 1 | **1 consolidado** |
| Branch strategy | trabajo en main | hu/<HU_ID> | hu/<HU_ID> |
| Smart resume | Parcial | Sí | Sí (refinement + plan + per-plugin) |
| GitHub Project sync | No | Sí | Sí (compatible) |
| Reglas duras | 5 R-rules DEHA | 4 FSD + 14 anti-patterns | Ambas + manifest=SSoT |

---

## §9. Quick reference de archivos canónicos

```
$ARTIFACTS_DIR/                                    (efímero, por run)
├── hu-original.md
├── hu-refinada.md                                 (refiner + planners)
├── plugin-manifest.yaml                           (plugin-planner + sub-pipeline)
├── plugin-work.yaml                               (sub-pipeline solo)
├── feature-plan-manifest.yaml                     (feature-planner + implementer)
├── tareas/F<NN>-<slug>.md                         (feature-planner + implementer)
├── task.md                                        (implementer)
├── task-result.yaml                               (implementer)
├── test-failures.md                               (gate determinista feedback)
├── playwright-evidence-<TASK_ID>.log              (gate playwright)
├── functional-evidence.log                        (final-validation)
├── playwright-final.log                           (final-validation)
├── pr-body.md                                     (build-pr-body)
├── merge-report.yaml                              (merger)
├── findings-{deha,fsd,plugin-system,test-coverage,security}.yaml (review agents)
├── auto-fix-plan.yaml + fixes-applied.yaml + fixes-reverted.yaml (review)
└── review-report.md + comment.md                  (review post)

<repo>/hubara_agency/.hubara/                      (durable, en repo)
├── spinal-files.yaml                              (setup manual)
├── project-context.md                             (setup manual)
├── drafts/idea-<ts>.md                            (idea-a-hu-hubara)
├── refinements/<HU_ID>-{tech,original}.md         (orquestador FASE 1)
├── plans/<HU_ID>/
│   ├── plugin-manifest.yaml                       (orquestador FASE 2)
│   └── feature-plans/<plugin>/
│       ├── feature-plan-manifest.yaml             (sub-pipeline FASE 1)
│       └── tareas/F<NN>-*.md                      (sub-pipeline FASE 1)
└── results/<HU_ID>/
    ├── plugin-<plugin_id>-result.yaml             (sub-pipeline FASE 3)
    └── feature-results/<plugin>/F<NN>-result.yaml (sub-pipeline until_bash)
```

---

## §10. Validación E2E (test del pipeline propio)

Estos tests son ~3-4 horas cada uno → NO van en CI; se corren MANUAL al
final de PR17/PR18 antes de declarar V1 estable.

### Test 1 — HU dummy single_plugin

```bash
archon workflow run idea-a-hu-hubara "agregar endpoint /healthcheck al plugin chats"
# Esperar approval → aprobar → esperar pipeline ~20 min
# Validar: PR creado contains endpoint, gates green, review comment
```

### Test 2 — HU dummy multi_plugin

```bash
archon workflow run idea-a-hu-hubara "agregar dashboard widget que muestre métricas del catálogo y de los chats"
# Esperar approval → aprobar
# Refinement detecta multi_plugin → plan emite 2 plugins (catalog + chats)
# Orquestador imprime 2 comandos de fan-out
# En 2 terminales: corré los comandos
# Esperar ambos terminen → "ready" al orquestador
# Validar: PR creado con commits de ambos plugins, gates green
```

### Test 3 — HU que toca shared file (merger)

```bash
archon workflow run idea-a-hu-hubara "agregar icono 'compass' al sistema y usarlo en chats + orders"
# Refinement marca requires_merger=true (Icon.tsx + entities/order si aplica)
# Plan emite 2 plugins (chats + orders) en mismo batch
# Operador corre 2 fan-out
# Después de "ready", orquestador invoca hubara-merger-archon
# Merger consolida ts_object_entries_append intents en Icon.tsx
# Validar: Icon.tsx tiene ambos icons (image + refresh) sin duplicación
```

### Criterio "V1 estable" (dispara PR19 deprecation)

- ≥3 HUs reales mergeadas exclusivamente con hu-hubara-pipeline
- ≥1 HU multi-plugin mergeada
- ≥0 fallback a exoclaw / frontend pipelines en últimas 4 semanas
- Review automático corrió ≥3 PRs sin falsos positivos críticos

---

**Fin README-hubara.md.** Tu pipeline está listo.
