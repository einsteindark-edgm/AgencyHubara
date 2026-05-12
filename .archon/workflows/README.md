# Pipeline DEHA + Archon — Guía operacional

Este pipeline transforma una HU (historia de usuario) en código de producción
para un agente exoclaw-temporal, vía 4 workflows de Archon coordinados por
file-system y convenciones git. Soporta paralelismo total entre agentes
programadores (uno por tarea atómica del DAG).

---

## 1. Componentes

### Skills (`.claude/skills/`)

| Skill | Rol | Escribe código? |
|-------|-----|-----------------|
| `exoclaw-tech-refiner-archon` | refinamiento técnico de la HU | no |
| `exoclaw-task-planner-archon` | descomposición en DAG de tareas + parallel_batches | no |
| `exoclaw-implementer-archon` | implementa UNA tarea (1 worktree por tarea) | **sí** |
| `exoclaw-merger-archon` | consolida wiring_intents de N tareas paralelas en spinal files | **sí** (sólo spinal files) |

### Workflows (`.archon/workflows/`)

| Workflow | Comando | Rol |
|----------|---------|-----|
| `refinar-hu.yaml` | `archon run refinar-hu "<input>"` | HU → refinamiento técnico |
| `planificar-hu.yaml` | `archon run planificar-hu "<HU-id>"` | refinamiento → DAG de tareas |
| `implementar-tarea.yaml` | `archon run implementar-tarea "<HU-id> F<NN>"` | una tarea → código |
| `implementar-hu.yaml` | `archon run implementar-hu "<HU-id>"` | orquestador: lanza batches paralelos + merger |

### Convenciones (`hubara_agency/.exoclaw/`)

| Archivo | Quién lo escribe | Quién lo lee |
|---------|------------------|--------------|
| `hubara_agency/.exoclaw/spinal-files.yaml` | el operador (1 vez por agente) | planner, implementer, merger |
| `hubara_agency/.exoclaw/refinements/<id>-tech.md` | refinar-hu | planificar-hu |
| `hubara_agency/.exoclaw/refinements/<id>-original.md` | refinar-hu | planificar-hu (opcional, fallback) |
| `hubara_agency/.exoclaw/plans/<id>/plan-manifest.yaml` | planificar-hu | implementar-tarea, implementar-hu |
| `hubara_agency/.exoclaw/plans/<id>/tareas/F<NN>-<slug>.md` | planificar-hu | implementar-tarea |
| `hubara_agency/.exoclaw/results/<id>/F<NN>-result.yaml` | implementar-tarea | implementar-hu (orquestador) |

---

## 2. Modelo de paths — `$ARTIFACTS_DIR` vs `hubara_agency/.exoclaw/`

Los dos paths conviven en cada workflow porque resuelven problemas distintos:

```
┌─────────────────────────────────────────────────────────────────────┐
│ $ARTIFACTS_DIR                                                       │
│   Path real: ~/.archon/workspaces/<owner>/<repo>/artifacts/runs/<id>│
│   Vida: SOLO durante un run de workflow                              │
│   Uso: la skill (genérica) lee/escribe con nombres canónicos         │
│        (hu-refinada.md, task.md, plan-manifest.yaml, etc.)           │
│   NO se commitea, NO se persiste                                     │
└─────────────────────────────────────────────────────────────────────┘
                            ↑
                            │ cp por nodos `cargar-*` / `persistir-*`
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│ hubara_agency/.exoclaw/                                                            │
│   Path real: <worktree-root>/hubara_agency/.exoclaw/                               │
│   Vida: hasta que el operador la borre del repo                      │
│   Uso: el workflow (sabe el HU id) persiste outputs para el próximo  │
│        workflow                                                       │
│   SE commitea, SE mergea a main para cross-workflow handoff          │
└─────────────────────────────────────────────────────────────────────┘
```

**Regla simple**: las skills nunca leen ni escriben en `hubara_agency/.exoclaw/` —
los nodos `cargar-*` y `persistir-*` del workflow hacen el bridge.

---

## 3. Timeline end-to-end de una HU

Cada `WT` = worktree (Archon crea uno por cada `archon run`).

```
TIEMPO ─────────────────────────────────────────────────────────────────────►

OP = operador (vos)        ARCHON = la CLI        SKILL = LLM dentro del workflow

┌──────────────────────────────────────────────────────────────────────────┐
│ FASE 1 — Refinamiento técnico                                             │
├──────────────────────────────────────────────────────────────────────────┤
│ OP      │ archon run refinar-hu "ruta/a/hu.md"                            │
│ ARCHON  │ crea WT_R (worktree desde main)                                 │
│ SKILL   │ lee $ARTIFACTS_DIR/hu-original.md                               │
│         │ escribe $ARTIFACTS_DIR/hu-refinada.md                           │
│ OP      │ revisa, da feedback en el loop hasta "aprobada"                 │
│ WORKFLOW│ nodo persistir-refinamiento:                                    │
│         │   cp $ARTIFACTS_DIR/hu-refinada.md WT_R/hubara_agency/.exoclaw/refinements/   │
│         │                                  <id>-tech.md                   │
│ OP      │ git -C WT_R add hubara_agency/.exoclaw/refinements/<id>-*.md                  │
│         │ git -C WT_R commit -m "<id>: refinamiento aprobado"             │
│         │ git push / merge a main                                         │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ FASE 2 — Planificación (DAG)                                              │
├──────────────────────────────────────────────────────────────────────────┤
│ OP      │ archon run planificar-hu "<HU-id>"                              │
│ ARCHON  │ crea WT_P (worktree desde main, ya incluye refinements)         │
│ WORKFLOW│ nodo cargar-refinamiento:                                       │
│         │   cp WT_P/hubara_agency/.exoclaw/refinements/<id>-tech.md                     │
│         │      $ARTIFACTS_DIR/hu-refinada.md                              │
│ SKILL   │ lee $ARTIFACTS_DIR/hu-refinada.md                               │
│         │ lee WT_P/hubara_agency/.exoclaw/spinal-files.yaml                             │
│         │ escribe $ARTIFACTS_DIR/plan-manifest.yaml                       │
│         │ escribe $ARTIFACTS_DIR/tareas/F<NN>-*.md                        │
│ OP      │ revisa, da feedback hasta "aprobado"                            │
│ WORKFLOW│ nodo persistir-plan:                                            │
│         │   cp $ARTIFACTS_DIR/plan-manifest.yaml + tareas/*               │
│         │      WT_P/hubara_agency/.exoclaw/plans/<id>/                                  │
│ OP      │ git -C WT_P add hubara_agency/.exoclaw/plans/<id>/                            │
│         │ git -C WT_P commit -m "<id>: plan aprobado (N features)"        │
│         │ git push / merge a main                                         │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ FASE 3 — Implementación con paralelismo                                   │
│ (este es el bucle de batches dirigido por implementar-hu)                 │
├──────────────────────────────────────────────────────────────────────────┤
│ OP      │ archon run implementar-hu "<HU-id>"                             │
│ ARCHON  │ crea WT_O (orquestador, desde main + refinements + plans)       │
│ WORKFLOW│ nodo cargar-plan: lee el manifest del plan                       │
│         │   identifica el próximo batch B<k> con tareas [F<NN1>, F<NN2>,] │
│ WORKFLOW│ FASE A — muestra al operador los N comandos a lanzar            │
│         │                                                                  │
│ OP      │ ──── abre N terminales nuevas ────                              │
│         │ Terminal 1: archon run implementar-tarea "<id> F<NN1>"          │
│         │ Terminal 2: archon run implementar-tarea "<id> F<NN2>"          │
│         │ ...                                                              │
│         │                                                                  │
│ ARCHON  │ por cada terminal, crea un WT_Ti independiente                  │
│         │ (todos branched desde main + previous batches mergeados)        │
│         │                                                                  │
│ SKILL   │ (en cada WT_Ti, en paralelo):                                   │
│         │   - lee $ARTIFACTS_DIR/task.md                                  │
│         │   - escribe código de feature en src/<agent>/... (new files)    │
│         │   - edita spinal files LOCALMENTE (para que sus tests pasen)    │
│         │   - escribe $ARTIFACTS_DIR/task-result.yaml con wiring_intents  │
│ WORKFLOW│ nodo persistir-resultado (en cada WT_Ti):                       │
│         │   cp $ARTIFACTS_DIR/task-result.yaml                            │
│         │      WT_Ti/hubara_agency/.exoclaw/results/<id>/F<NNi>-result.yaml             │
│ OP      │ (en cada WT_Ti, MODO ORQUESTADO):                               │
│         │   git -C WT_Ti add <files_created> hubara_agency/.exoclaw/results/<id>/...    │
│         │     # ⚠️ NO incluir spinal files — son throwaway                │
│         │   git -C WT_Ti commit -m "<id> F<NNi>: feature code"            │
│         │   git push / merge a main                                       │
│         │                                                                  │
│ OP      │ ──── vuelve a la terminal del orquestador WT_O ────             │
│         │ responde "ready" en el loop del orquestador                     │
│         │                                                                  │
│ WORKFLOW│ FASE B — orquestador:                                           │
│         │   B.0 git fetch + git merge --ff-only origin/main               │
│         │       (WT_O ahora ve los new files + results de las N tareas)   │
│         │   B.2 verifica que los N result.yaml tengan status: passed      │
│         │   B.3 cp hubara_agency/.exoclaw/results/<id>/F*.yaml                          │
│         │        $ARTIFACTS_DIR/batch-results/                            │
│         │   B.4 valida que los spinal files estén en main-state           │
│         │   B.5 invoca skill exoclaw-merger-archon                        │
│ SKILL   │   merger lee $ARTIFACTS_DIR/batch-results/F*.yaml               │
│         │   aplica wiring_intents a WT_O/src/<agent>/worker.py,           │
│         │     composition.py, contracts.py, workspace/TOOLS.md, etc.      │
│         │   escribe $ARTIFACTS_DIR/merge-report.yaml                      │
│ OP      │ revisa $ARTIFACTS_DIR/merge-report.yaml + git diff              │
│         │ git -C WT_O add <spinal files modificados por el merger>        │
│         │ git -C WT_O commit -m "<id> B<k>: merger consolidó wiring"      │
│         │ git push / merge a main                                         │
│         │                                                                  │
│ OP      │ responde "next" en el orquestador                               │
│ WORKFLOW│ FASE C — git fetch + git merge --ff-only origin/main            │
│         │   identifica el siguiente batch B<k+1> → vuelve a FASE A        │
│         │                                                                  │
│ ... (bucle hasta que todos los batches del manifest están completos) ...  │
│                                                                            │
│ WORKFLOW│ nodo cerrar-pipeline:                                           │
│         │   reporte final, tabla de tareas, sugerencias (pytest full,     │
│         │   crear PR consolidado, cleanup de worktrees)                   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Lifecycle de los worktrees

```
                          MAIN BRANCH (la rama base)
                          ════════════════════════════
                          │
   archon run refinar-hu ─┼─► WT_R ─────────► commit + merge ──┐
                          │                                     │
                          │   ◄──────────── refinements en main ┘
                          │
archon run planificar-hu ─┼─► WT_P ─────────► commit + merge ──┐
                          │                                     │
                          │   ◄──────────── plans en main ──────┘
                          │
 archon run implementar-hu┼─► WT_O ──────────────────────────────┐
                          │   (vive todo el pipeline)             │
                          │                                       │
                          │   ┌── WT_T1 ──► commit + merge ──┐    │
                          │   │   (terminal 1 paralelo)      │    │
                          │   │                              │    │
                          │   ├── WT_T2 ──► commit + merge ──┤    │
                          │   │   (terminal 2 paralelo)      │    │
                          │   │                              ▼    │
                          │   └── WT_T3 ──► commit + merge ─►main │
                          │                                       │
                          │   WT_O git pull ◄────────────────────┘
                          │   merger en WT_O ─► commit + merge ──┐
                          │                                       │
                          │   ◄────── batch B<k> consolidado ─────┘
                          │   WT_O git pull
                          │   (loop al siguiente batch)
                          │
                          ▼
              (al final: HU implementada en main)
```

**Reglas de worktrees**:

1. Cada `archon run` crea un worktree fresh desde la rama base actual.
2. El worktree del orquestador (`WT_O`) **vive todo el pipeline**. Por eso
   necesita `git pull` explícito entre batches (FASE B.0 y FASE C del
   workflow lo hacen).
3. Los worktrees de tareas (`WT_T*`) **son efímeros**: al terminar y commitear,
   se pueden borrar. Sus spinal-file changes son throwaway de todos modos.
4. Si un worktree de tarea queda colgado (status: blocked), el operador puede
   iterarlo (`archon run implementar-tarea "<id> F<NN>"` re-entra al loop)
   o descartarlo y replantear el plan via `planificar-hu`.

---

## 5. Manual del operador

### Setup inicial (1 vez por repo / agente)

```bash
# 1. Asegurarse de que hubara_agency/.exoclaw/spinal-files.yaml está completo y commiteado.
#    Editar a mano para listar los archivos compartidos del agente.
git add hubara_agency/.exoclaw/spinal-files.yaml
git commit -m "exoclaw: spinal files convention"
git push
```

### Por cada HU

```bash
# FASE 1 — refinar
archon run refinar-hu "specs/HU-XYZ.md"
# (iterás hasta "aprobada")
# Al final el workflow imprime el HU id y los comandos git. Ejecutalos:
git add hubara_agency/.exoclaw/refinements/<id>-*.md
git commit -m "<id>: refinamiento aprobado"
git push

# FASE 2 — planificar
archon run planificar-hu "<id>"
# (iterás hasta "aprobado")
git add hubara_agency/.exoclaw/plans/<id>/
git commit -m "<id>: plan aprobado"
git push

# FASE 3 — implementar (paralelo, orquestado)
archon run implementar-hu "<id>"
# El workflow te va guiando batch por batch:
#   - Te imprime los N comandos para abrir en N terminales
#   - Vos los corrés, esperás, commiteás cada uno (SIN spinal files)
#   - Volvés y respondés "ready"
#   - El workflow invoca al merger y te da los comandos git para mergear consolidado
#   - Respondés "next" y avanza al siguiente batch
# Hasta terminar.

# (opcional al final)
uv run pytest tests/ -q
gh pr create --title "<id>: <título>"
```

### Por cada tarea (modo standalone, sin orquestador)

```bash
archon run implementar-tarea "<id> F<NN>"
# (iterás hasta "ok")
# Modo standalone: commiteá TODO (incluyendo spinal files):
git add <files_created> <files_modified> hubara_agency/.exoclaw/results/<id>/F<NN>-result.yaml
git commit -m "<id> F<NN>: <título>"
git push
```

---

## 6. Troubleshooting

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| `planificar-hu` no encuentra `hubara_agency/.exoclaw/refinements/<id>-tech.md` | no mergeaste el refinamiento a main antes de correr planificar-hu | mergeá a main + re-lanzá |
| `implementar-tarea` aborta con `depends_on_missing` | una tarea upstream del DAG no fue implementada / mergeada | implementá la upstream primero (respetá topological order del manifest) |
| `implementar-hu` FASE B.4 detecta cambios en spinal files vs main | commiteaste spinal files de una tarea paralela | `git checkout origin/main -- <spinal-files>` + responde "ready" otra vez |
| `implementar-hu` FASE B.0 falla con conflicto al `git merge --ff-only` | el WT_O tiene cambios locales sin commitear | investigá con `git status`; probablemente el merger emitió cambios que no commiteaste antes de decir "next" |
| El merger devuelve `status: failed` | colisión nombre-igual-contenido-distinto en algún spinal file | revisá `merge-report.yaml` errors[]; típicamente dos tareas declararon factories con mismo `name` y bodies diferentes → re-correr planificar para resolver |
| El merger devuelve `status: partial` | un spinal file quedó sintácticamente inválido tras aplicar intents | revisá `merge-report.yaml` warnings[]; el archivo afectado fue restaurado a main, las otras consolidaciones quedan firmes |
| `implementar-tarea` marca `requires_planner_update` | la tarea quería mutar (no append) un entry existente en spinal file | volvé a `planificar-hu`, re-decompone para evitar la mutación, mergeá el nuevo plan, retomá la implementación |
| Un batch tiene >5 tareas y no quiero correr tantas en paralelo | el planner emitió warning pero el operador decide | corré sub-batches: lanzá algunas tareas, esperá, "ready", merger, "next" con las restantes (en realidad el orquestador no soporta esto nativamente — vas a tener que decirle que sólo lanzaste un subset y manejarlo manualmente) |

---

## 7. Quick reference de archivos canónicos

```
$ARTIFACTS_DIR/                       (efímero, por run)
├── hu-original.md                    (refinar-hu)
├── hu-refinada.md                    (refinar-hu + planificar-hu)
├── plan-manifest.yaml                (planificar-hu + implementar-tarea + implementar-hu)
├── tareas/F<NN>-<slug>.md            (planificar-hu)
├── task.md                           (implementar-tarea)
├── task-result.yaml                  (implementar-tarea)
├── batch-results/F<NN>-result.yaml   (implementar-hu, staging para merger)
└── merge-report.yaml                 (merger via implementar-hu)

<repo>/hubara_agency/.exoclaw/                      (durable, en el repo)
├── spinal-files.yaml                 (setup manual, 1 vez)
├── refinements/<id>-tech.md          (persistido por refinar-hu)
├── refinements/<id>-original.md      (persistido por refinar-hu)
├── plans/<id>/plan-manifest.yaml     (persistido por planificar-hu)
├── plans/<id>/tareas/F<NN>-*.md      (persistido por planificar-hu)
└── results/<id>/F<NN>-result.yaml    (persistido por implementar-tarea)
```
