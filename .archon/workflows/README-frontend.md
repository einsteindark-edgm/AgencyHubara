# Pipeline FSD + Archon — Frontend (guía operacional)

Este pipeline transforma una HU de frontend (FSD: Vite + React 19 + TanStack
Query + Zod + Tailwind v4) en código de producción para `frontend_dashboard/`,
vía workflows de Archon coordinados por file-system, convenciones git, y
opcionalmente GitHub Projects. Soporta paralelismo total entre agentes
programadores (uno por tarea atómica del DAG).

Es el **espejo del pipeline exoclaw** (`README.md`) pero adaptado a FSD y con
una variante automatizada extra (`hu-frontend-pipeline`).

---

## 1. Componentes

### Skills (`.claude/skills/`)

| Skill | Rol | Escribe código? |
|-------|-----|-----------------|
| `frontend-tech-refiner-archon` | refinamiento técnico de la HU | no |
| `frontend-task-planner-archon` | descomposición en DAG de tareas + parallel_batches | no |
| `frontend-implementer-archon` | implementa UNA tarea (1 worktree por tarea) | **sí** |
| `frontend-merger-archon` | consolida wiring_intents de N tareas paralelas en spinal files | **sí** (sólo spinal files) |

### Workflows (`.archon/workflows/`)

**7 workflows totales** — 1 entrada interactiva (idea → issue) + 4 fases interactivas (back-up) + super-pipeline auto + code review automático:

| Workflow | Comando | Rol | Auto |
|----------|---------|-----|------|
| `idea-a-hu-frontend` | `archon workflow run idea-a-hu-frontend "<idea>"` | **ENTRADA**: idea → HU refinada → Issue en GitHub → Project board (status "Idea refined") → opcional: dispara pipeline | parcial (2 approval gates) |
| `refinar-hu-frontend` | `archon workflow run refinar-hu-frontend "<input>"` | HU → refinamiento técnico (FSD) | no (loop interactivo) |
| `planificar-hu-frontend` | `archon workflow run planificar-hu-frontend "<HU-id>"` | refinamiento → DAG de tareas | no |
| `implementar-tarea-frontend` | `archon workflow run implementar-tarea-frontend "<HU-id> F<NN>"` | una tarea → código | no |
| `implementar-hu-frontend` | `archon workflow run implementar-hu-frontend "<HU-id>"` | orquestador: terminales paralelas + merger | no (manual fan-out) |
| `hu-frontend-pipeline` | `archon workflow run hu-frontend-pipeline "<issue-url-or-HU-id>"` | super-pipeline E2E (secuencial) + GitHub PR + dispara review auto | **sí** |
| `review-pr-frontend` | `archon workflow run review-pr-frontend "<PR_URL>"` | 5 agentes de code review especializados + auto-fix CRITICAL/HIGH + PR comment | **sí** (auto-disparado desde `hu-frontend-pipeline`) |

**Chain end-to-end completo (camino feliz)**:

```
idea-a-hu-frontend (idea cruda → vos)
    ├─ normalize-input
    ├─ refinar-hu-producto (loop interactivo, vos iterás hasta "ok")
    ├─ validate-hu
    ├─ save-draft (frontend_dashboard/.frontend/drafts/idea-<ts>.md)
    ├─ [APPROVAL GATE #1] aprobar-publicacion
    ├─ crear-issue (gh issue create)
    ├─ agregar-a-project (gh project item-add + status "Idea refined")
    └─ [APPROVAL GATE #2] gate-lanzar-pipeline
            ↓ (aprobás)
            lanzar-pipeline (background)
                ↓
hu-frontend-pipeline (Issue URL)  ─────────────── card → "Refining"
    ├─ refinar-auto             ─────────────── card → "Refined"
    ├─ planificar-auto          ─────────────── card → "Planned"
    ├─ implementar-secuencial   ─────────────── card → "Implementing"
    │  (loop con until_bash determinista)
    ├─ final-validation (npm test + tsc + build)
    ├─ create-pr (--body-file)  ─────────────── card → "Done — PR ready"
    └─ trigger-review (lanza review-pr-frontend en background)
                          │
                          └─→ review-pr-frontend (PR URL)
                                  ├─ fetch-pr + checkout-branch + fetch-diff
                                  ├─ classify (haiku, decide qué agentes corren)
                                  ├─ 5 agentes en paralelo (cuando when:true)
                                  │   ├─ fsd-compliance
                                  │   ├─ type-zod-boundary
                                  │   ├─ react-practices
                                  │   ├─ test-coverage
                                  │   └─ security
                                  ├─ synthesize
                                  ├─ auto-fix CRITICAL/HIGH (solo archivos del PR; revierte si tests rompen)
                                  ├─ commit-fixes (selectivo + pull --rebase retry)
                                  └─ post-comment al PR
```

### Diseño de nodos (siguiendo Archon best practices)

Todos los workflows siguen el patrón **"deterministic nodes for deterministic work, AI nodes for reasoning"** que documenta Archon:

| Tipo de trabajo | Tipo de nodo | Ejemplos en estos workflows |
|---|---|---|
| `cp`, `mkdir`, `git add/commit/push`, `gh` CLI, parseo de YAML, validación de archivos | `bash:` | `preparar-input`, `cargar-refinamiento`, `cargar-tarea`, `cargar-plan`, `persistir-*`, `commit-*`, `final-validation`, `create-pr`, `check-prereqs`, `validate-*` |
| JSON manipulation, generación de IDs/slugs, construcción de PR body | `script:` (bun) | `gen-hu-id`, `build-pr-body` |
| Refinamiento, planificación, implementación de código (necesita AI con feedback iterativo) | `loop:` con `skills:` | `refinar`, `planificar`, `implementar`, `ejecutar-pipeline`, `refinar-auto`, `planificar-auto`, `implementar-secuencial` |
| Fail-fast cuando un precondition no se cumple | `cancel:` con `when:` | `cancel-bad-prereqs`, `cancel-bad-refinement`, `cancel-bad-plan`, `cancel-on-implement-error`, `cancel-on-final-validation-fail` |
| Skip de fases ya completas (smart resume) | `when:` sobre output de bash | `load-refinement-if-resume`, `refinar-auto`, `load-plan-if-resume`, `planificar-auto` |
| Updates al GitHub Project que NO deben matar el pipeline si fallan | `bash:` + `trigger_rule: all_done` | `project-set-refining`, `project-set-refined`, `project-set-planned`, `project-set-implementing`, `project-set-done` |
| Validación estructurada que después conditiona via `$nodeId.output.field` | `bash:` o `script:` + `output_format:` (esquema JSON) | `resolve-input`, `gen-hu-id`, `detect-resume-state` |

**Por qué importa**: AI nodes son caros (~$0.01-0.10 cada uno), lentos (5-30s por llamada),
y pueden alucinar. Para tareas con respuesta correcta (un `cp`, un `git commit`), bash es
gratis, determinístico y en ms. Solo donde realmente necesitamos razonamiento (refinar,
planificar, implementar código) usamos AI.

### Convenciones (`frontend_dashboard/.frontend/`)

| Archivo | Quién lo escribe | Quién lo lee |
|---------|------------------|--------------|
| `spinal-files.yaml` | el operador (1 vez por frontend) | planner, implementer, merger |
| `project-context.md` | el operador (1 vez) | TODAS las skills (lo leen primero) |
| `github-project-config.yaml` | el operador (1 vez, opcional) | hu-frontend-pipeline |
| `refinements/<id>-tech.md` | refinar-hu-frontend / hu-frontend-pipeline | planificar-hu-frontend |
| `refinements/<id>-original.md` | refinar-hu-frontend / hu-frontend-pipeline | planificar-hu-frontend (fallback) |
| `plans/<id>/plan-manifest.yaml` | planificar-hu-frontend / hu-frontend-pipeline | implementar-tarea-frontend, implementar-hu-frontend |
| `plans/<id>/tareas/F<NN>-<slug>.md` | planificar-hu-frontend / hu-frontend-pipeline | implementar-tarea-frontend |
| `results/<id>/F<NN>-result.yaml` | implementar-tarea-frontend / hu-frontend-pipeline | implementar-hu-frontend, hu-frontend-pipeline |

---

## 2. Dos modos de uso

### Modo A — INTERACTIVO (igual que exoclaw)

Mismo flujo manual de 3 fases, con gates humanos entre cada una. Útil cuando
estás iterando arquitectura y querés revisar refinamiento + plan antes de
implementar.

```bash
# FASE 1 — refinar (loop interactivo)
archon workflow run refinar-hu-frontend "specs/HU-XYZ.md"
# Iterás hasta "aprobada". Al final commiteás:
git add frontend_dashboard/.frontend/refinements/<id>-*.md
git commit -m "<id>: refinamiento (frontend) aprobado"
git push

# FASE 2 — planificar (loop interactivo)
archon workflow run planificar-hu-frontend "<id>"
# Iterás hasta "aprobado". Al final commiteás:
git add frontend_dashboard/.frontend/plans/<id>/
git commit -m "<id>: plan (frontend) aprobado"
git push

# FASE 3 — implementar (orquestador con fan-out manual de terminales)
archon workflow run implementar-hu-frontend "<id>"
# El workflow te guía: te dice los N comandos a lanzar en N terminales,
# vos los corrés, esperás, commiteás (sin spinal files), volvés y
# respondés "ready" para invocar al merger.
```

### Modo B — AUTOMATIZADO SECUENCIAL (nuevo, exclusivo de frontend)

Un solo comando hace todo de punta a punta. Sin gates humanos. Las tareas
corren **una después de la otra en el mismo worktree** (no en paralelo).

**Por qué secuencial y no paralelo**: el fan-out paralelo desde adentro de un
workflow Archon (vía `archon workflow run X &` + `wait`) NO está garantizado
por la plataforma. El modelo exoclaw lo evita explícitamente con fan-out
manual del operador. Para el modo auto elegimos confiabilidad sobre velocidad:
todas las tareas corren en el worktree del pipeline, una a la vez, viendo los
edits de las anteriores. El merger NO se invoca (no hace falta — los spinal
files se editan en place porque cada tarea ve el estado dejado por la anterior).

**Limitación**: para HUs grandes (>5-8 tareas, >30 min total) el modo auto
puede chocar contra el timeout del workflow. Para esos casos, usá el modo A
con fan-out manual de terminales.

```bash
# Setup (1 sola vez por frontend):
#   - Asegurate de que .frontend/spinal-files.yaml y .frontend/project-context.md
#     existan en MAIN (committeados).
#   - Asegurate de que los 4 skills frontend-*-archon estén en .claude/skills/
#     en MAIN.
#   - Asegurate de que los 5 workflows frontend estén en .archon/workflows/ en MAIN.
#   - (Opcional) Configurá .frontend/github-project-config.yaml — ver
#     `.frontend/github-project-config.yaml.example`.

# Por cada HU:
archon workflow run hu-frontend-pipeline "https://github.com/<owner>/<repo>/issues/42"

# El pipeline:
#   1. Bootstrap: chequea pre-requisitos (gh, npm, bun, jq, .frontend/*, skills, node_modules).
#   2. Lee el body del Issue como HU. Genera HU_ID (con timestamp HHMMSS para
#      evitar colisiones). Crea branch hu/<HU_ID> desde origin/main y lo pushea.
#   3. FASE 1 — Refinar automático (skill frontend-tech-refiner-archon).
#      Validación permisiva del output (1 retry si falla). Commit + push.
#   4. FASE 2 — Planificar automático (skill frontend-task-planner-archon).
#      Validación: task_count entre 1 y 12 (cap conservador). Commit + push.
#   5. FASE 3 — Implementar SECUENCIAL. Loop con `until_bash` determinista:
#      a. AI escribe código + tests + task-result.yaml.
#      b. until_bash (bash, no AI) persiste result, commit selectivo, push con pull-rebase retry.
#      c. Si STATUS != passed → pipeline-error.yaml + exit del loop.
#      d. Si todas las tareas passed → exit 0 (loop termina exitoso).
#   6. FASE 4 — Si todo passed, validación final (npm test + tsc -b + npm run build
#      con `set -o pipefail` para no perder errores), `gh pr create --body-file ...`
#      (--body-file evita interpretación de backticks en pr-body.md).
#   7. trigger-review — dispara `review-pr-frontend` en background con `env -u CLAUDECODE`
#      (evita el "silent hang" del nested archon-in-claude-code).
#   8. (Si GitHub Project config existe) actualiza card al estado correspondiente
#      en cada fase (fail-soft — un error de Project no mata el pipeline).

# Post-pipeline (corre solo, ~3-5 min):
#   - review-pr-frontend hace checkout del branch hu/<HU_ID>
#   - classifier (haiku) decide qué de los 5 agentes correr
#   - agentes corren en paralelo (fsd-compliance, type-zod-boundary, react-practices,
#     test-coverage, security)
#   - synthesize consolida findings
#   - auto-fix arregla CRITICAL/HIGH (revierte si rompe tests)
#   - postea comment al PR con los findings

# Vos:
#   - Revisás el PR cuando termina.
#   - Squash-merge si todo OK.
#   - El issue se cierra automáticamente (Closes <url> en el body del PR).

# Si algo falla a la mitad:
#   - El pipeline escribe pipeline-error.yaml con la remediation específica.
#   - El branch hu/<HU_ID> queda PUSHADO en origin con el progreso hasta ese punto.
#   - Vos retomás manualmente con el workflow interactivo:
#       archon workflow run refinar-hu-frontend "<HU_ID>"          # si falló FASE 1
#       archon workflow run planificar-hu-frontend "<HU_ID>"        # si falló FASE 2
#       archon workflow run implementar-tarea-frontend "<HU_ID> F<NN>"  # si falló una tarea
#   - Una vez resuelto y mergeado a hu/<HU_ID>, re-lanzás el pipeline:
#       archon workflow run hu-frontend-pipeline "<HU_ID>"
#     El bootstrap detecta el branch existente + las fases ya completas y
#     retoma desde donde quedó.
```

---

## 3. Diferencias clave vs el pipeline exoclaw

| Aspecto | Pipeline exoclaw (Python) | Pipeline frontend (FSD) |
|---|---|---|
| Lenguaje target | Python (uv workspace) | TypeScript + React (npm + Vite) |
| Hard rules | R-DET, R-JSON, R-STATELESS, R-HEARTBEAT, R-DIP | 4 import rules FSD + 14 anti-patterns |
| Comando de test | `cd hubara_agency && uv run pytest ...` | `cd frontend_dashboard && npm test ...` |
| Spinal files típicos | `worker.py`, `composition.py`, `contracts.py`, `workspace/*.md` | `pages/<X>.tsx`, `app/providers/index.tsx`, `index.css`, barrels |
| Wiring intent kinds | `register_tool_extension`, `factory_function`, `dataclass_def`, `markdown_section`, … | `page_feature_mount`, `provider_wrap`, `tailwind_token`, `barrel_export`, `zod_schema_def`, … |
| Modo auto-pipeline | ❌ no existe (solo interactivo) | ✅ `hu-frontend-pipeline` (secuencial) |
| Paralelismo en modo auto | n/a | ❌ secuencial (1 tarea a la vez en el worktree del pipeline) |
| Paralelismo en modo interactivo | ✅ N terminales manuales + merger | ✅ idéntico (N terminales + merger) |
| GitHub Projects sync | ❌ | ✅ opcional (vía `.frontend/github-project-config.yaml`, fail-soft) |
| Branch strategy | trabajo en main directo (commits manuales) | branch `hu/<HU_ID>` aislado, PR al final |
| Smart resume | n/a | ✅ pipeline re-lanzado detecta fases completas y retoma |

---

## 4. Modelo de paths

Idéntico a exoclaw pero con `frontend_dashboard/.frontend/` en vez de
`hubara_agency/.exoclaw/`:

```
$ARTIFACTS_DIR (efímero, ~/.archon/workspaces/.../artifacts/runs/<id>/)
├── hu-original.md
├── hu-refinada.md
├── plan-manifest.yaml
├── tareas/F<NN>-<slug>.md
├── task.md
├── task-result.yaml
├── batch-results/F<NN>-result.yaml
├── merge-report.yaml
├── project-context.md           ← copiado del repo en cada cargar-*
├── spinal-files.yaml            ← idem
├── github-project-config.yaml   ← solo en hu-frontend-pipeline si existe
└── pipeline-state.yaml          ← solo en hu-frontend-pipeline (telemetría)

<repo>/frontend_dashboard/.frontend/ (durable, en el repo)
├── spinal-files.yaml
├── project-context.md
├── github-project-config.yaml (opcional)
├── refinements/<id>-tech.md
├── refinements/<id>-original.md
├── plans/<id>/plan-manifest.yaml
├── plans/<id>/tareas/F<NN>-*.md
└── results/<id>/F<NN>-result.yaml
```

---

## 5. Cuándo usar cada modo

| Caso | Modo recomendado |
|------|------------------|
| HU nueva, no estoy seguro de la arquitectura | Modo A (interactivo) — revisás refinamiento + plan antes de implementar |
| HU clara, equipo conoce la app | Modo B (pipeline auto) — un comando y café |
| HU con riesgo alto (cambia muchos features) | Modo A — humano en cada gate |
| HU rutinaria (nuevo CRUD, nuevo modal) | Modo B |
| Operador no está cerca del teclado | Modo B con GitHub Projects + notif al terminar el PR |
| HU bloqueada en producción urgente | Modo A — control granular, sin sorpresas |

El modo B no reemplaza al modo A. Coexisten: el modo B internamente usa
las mismas skills, y si falla cae a las versiones interactivas para que el
operador retome.

---

## 6. Setup inicial (1 vez por repo)

CRÍTICO: Archon corre cada workflow en un worktree fresh desde `origin/main`.
Todo lo que el pipeline necesita TIENE que estar committeado en main antes
de la primera corrida. Los pre-requisitos:

```bash
# 1. Convenciones del frontend committeadas en main:
ls frontend_dashboard/.frontend/
#   → spinal-files.yaml                       ✓ obligatorio
#   → project-context.md                      ✓ obligatorio
#   → github-project-config.yaml.example      (template, no se usa runtime)
#   → github-project-config.yaml              (opcional, si querés Project sync)

# 2. 4 skills frontend en .claude/skills/, committeados en main:
ls .claude/skills/ | grep frontend-.*-archon
#   → frontend-tech-refiner-archon            ✓ obligatorio
#   → frontend-task-planner-archon            ✓ obligatorio
#   → frontend-implementer-archon             ✓ obligatorio
#   → frontend-merger-archon                  ✓ obligatorio (solo modo interactivo)

# 3. 7 workflows frontend en .archon/workflows/, committeados en main:
ls .archon/workflows/ | grep frontend
#   → idea-a-hu-frontend.yaml                 ✓ obligatorio (entrada — idea → issue)
#   → refinar-hu-frontend.yaml                ✓ obligatorio
#   → planificar-hu-frontend.yaml             ✓ obligatorio
#   → implementar-tarea-frontend.yaml         ✓ obligatorio
#   → implementar-hu-frontend.yaml            ✓ obligatorio (modo interactivo)
#   → hu-frontend-pipeline.yaml               ✓ obligatorio (modo auto)
#   → review-pr-frontend.yaml                 ✓ obligatorio (code review post-PR)

# 4. Pre-requisitos de runtime (el pipeline los valida en check-prereqs):
gh auth status              # gh autenticado (lee Issue + crea PR + comenta)
command -v node && command -v npm    # node + npm
command -v bun              # bun (para nodos script:)
command -v jq               # jq (para parseo JSON en bash)
# Si falta gh: gh auth login
# Si falta bun: brew install bun  (o curl -fsSL https://bun.sh/install | bash)
# Si falta jq:  brew install jq

# 5. (Opcional) GitHub Project config para el modo auto:
cp frontend_dashboard/.frontend/github-project-config.yaml.example \
   frontend_dashboard/.frontend/github-project-config.yaml
# Edita los IDs siguiendo las instrucciones inline del archivo.

# 6. Commit todo lo nuevo a main:
git add .claude/skills/frontend-*-archon \
        .archon/workflows/*frontend*.yaml \
        .archon/workflows/README-frontend.md \
        frontend_dashboard/.frontend/spinal-files.yaml \
        frontend_dashboard/.frontend/project-context.md \
        frontend_dashboard/.frontend/github-project-config.yaml.example
git commit -m "frontend pipeline: skills + workflows + conventions"
git push origin main

# 7. (Opcional) Si vas a usar el modo auto, asegurate de que el frontend
# tenga node_modules (el pipeline lo instala si falta, pero es más rápido
# tenerlo):
cd frontend_dashboard && npm install && cd ..
```

**Verificación final** antes de la primera corrida:

```bash
# Que el worktree fresh desde origin/main tendría todo:
git fetch origin main
git diff --name-only origin/main -- .claude/ .archon/ frontend_dashboard/.frontend/
# Si devuelve archivos, faltan commits o pushes.
```

---

## 7. Troubleshooting (frontend-específico)

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| `hu-frontend-pipeline` aborta en bootstrap con "FATAL: falta..." | falta algo no committeado en main (skill, workflow, .frontend/) | `git status` en el repo principal; commitealo y empujalo a main; re-lanzá |
| `hu-frontend-pipeline` falla en FASE 1 con "validation_failed_after_retry" | el refinement auto quedó incompleto tras 2 intentos | retomar interactivo: `archon workflow run refinar-hu-frontend "<HU_ID>"`; commiteá; re-lanzá el pipeline |
| `hu-frontend-pipeline` falla en FASE 2 con "task_count > 12" o "task_count == 0" | la HU es demasiado grande o demasiado vaga para auto | retomar interactivo: `archon workflow run planificar-hu-frontend "<HU_ID>"`; re-decomponé manualmente |
| `hu-frontend-pipeline` falla en FASE 3 con "failed_task: F<NN>" | una tarea no pasó tests o quedó blocked | retomar interactivo: `archon workflow run implementar-tarea-frontend "<HU_ID> F<NN>"`; iterá con feedback; mergeá a hu/<HU_ID>; re-lanzá pipeline |
| `planificar-hu-frontend` no encuentra `.frontend/refinements/<id>-tech.md` | refinement no mergeado al base branch | en modo interactivo: `git push` + merge a main; en modo auto: el pipeline ya pushea automáticamente, validar que el branch hu/<HU_ID> esté visible |
| `npm test` rojo en validación final pre-PR (FASE 4) | regresión que ningún test §10 individual capturó | `cd frontend_dashboard && npm test` localmente, identificar test fallido, fixearlo a mano en hu/<HU_ID>, re-lanzar pipeline |
| `gh pr create` falla con auth | `gh auth status` muestra desconectado | `gh auth login` + `archon workflow run hu-frontend-pipeline "<HU_ID>"` (retoma) |
| Card del Project no se actualiza | `github-project-config.yaml` con IDs incorrectos | NO mata el pipeline (fail-soft). Revisá `$ARTIFACTS_DIR/project-sync.log` durante el run; corregí los IDs con `gh project field-list <N> --format json` |
| Modo auto se queda lento (>30 min) | HU con muchas tareas; secuencial es naturalmente más lento que paralelo | abortá; usá modo A (interactivo) con fan-out manual de terminales para esta HU |
| `node_modules` recreado en cada corrida | Archon worktree fresh no preserva node_modules | el bootstrap del pipeline corre `npm install --no-audit --no-fund` automáticamente (lento la 1ª vez, ~30s) |
| Bootstrap dice "hu/<HU_ID> local divergió de origin" | re-lanzaste pipeline pero alguien commiteó cosas raras al branch | a mano: `git checkout hu/<HU_ID>; git status`; resolver / discardear cambios locales; re-lanzar |

---

## 8. Quick reference de artifacts

```
$ARTIFACTS_DIR/                       (efímero, por run)
├── hu-original.md                    (todos los workflows)
├── hu-refinada.md                    (refinar + planificar + pipeline)
├── plan-manifest.yaml                (planificar + implementar-* + pipeline)
├── tareas/F<NN>-<slug>.md            (planificar + pipeline)
├── task.md                           (implementar-tarea-frontend + pipeline)
├── task-result.yaml                  (implementar-tarea-frontend + pipeline)
├── batch-results/F<NN>-result.yaml   (solo implementar-hu-frontend, staging del merger)
├── merge-report.yaml                 (solo implementar-hu-frontend, output del merger)
├── project-context.md                (todos los workflows, staging)
├── spinal-files.yaml                 (todos los workflows, staging)
├── github-project-config.yaml        (solo pipeline, si existe)
├── pipeline-state.yaml               (solo pipeline, telemetría)
├── pipeline-error.yaml               (solo pipeline, si algo falla)
└── project-sync.log                  (solo pipeline, warnings de gh project, fail-soft)

<repo>/frontend_dashboard/.frontend/  (durable)
├── spinal-files.yaml
├── project-context.md
├── github-project-config.yaml        (opcional)
├── refinements/<id>-{tech,original}.md
├── plans/<id>/{plan-manifest.yaml,tareas/F<NN>-*.md}
└── results/<id>/F<NN>-result.yaml
```
