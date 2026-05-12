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
| `refinar-hu.yaml` | `archon workflow run refinar-hu "<input>"` | HU → refinamiento técnico |
| `planificar-hu.yaml` | `archon workflow run planificar-hu "<HU-id>"` | refinamiento → DAG de tareas |
| `implementar-tarea.yaml` | `archon workflow run implementar-tarea "<HU-id> F<NN>"` | una tarea → código |
| `implementar-hu.yaml` | `archon workflow run implementar-hu "<HU-id>"` | orquestador: lanza batches paralelos + merger |

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
│ OP      │ archon workflow run refinar-hu "ruta/a/hu.md"                            │
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
│ OP      │ archon workflow run planificar-hu "<HU-id>"                              │
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
│ OP      │ archon workflow run implementar-hu "<HU-id>"                             │
│ ARCHON  │ crea WT_O (orquestador, desde main + refinements + plans)       │
│ WORKFLOW│ nodo cargar-plan: lee el manifest del plan                       │
│         │   identifica el próximo batch B<k> con tareas [F<NN1>, F<NN2>,] │
│ WORKFLOW│ FASE A — muestra al operador los N comandos a lanzar            │
│         │                                                                  │
│ OP      │ ──── abre N terminales nuevas ────                              │
│         │ Terminal 1: archon workflow run implementar-tarea "<id> F<NN1>"          │
│         │ Terminal 2: archon workflow run implementar-tarea "<id> F<NN2>"          │
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
   archon workflow run refinar-hu ─┼─► WT_R ─────────► commit + merge ──┐
                          │                                     │
                          │   ◄──────────── refinements en main ┘
                          │
archon workflow run planificar-hu ─┼─► WT_P ─────────► commit + merge ──┐
                          │                                     │
                          │   ◄──────────── plans en main ──────┘
                          │
 archon workflow run implementar-hu┼─► WT_O ──────────────────────────────┐
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
   iterarlo (`archon workflow run implementar-tarea "<id> F<NN>"` re-entra al loop)
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
archon workflow run refinar-hu "specs/HU-XYZ.md"
# (iterás hasta "aprobada")
# Al final el workflow imprime el HU id y los comandos git. Ejecutalos:
git add hubara_agency/.exoclaw/refinements/<id>-*.md
git commit -m "<id>: refinamiento aprobado"
git push

# FASE 2 — planificar
archon workflow run planificar-hu "<id>"
# (iterás hasta "aprobado")
git add hubara_agency/.exoclaw/plans/<id>/
git commit -m "<id>: plan aprobado"
git push

# FASE 3 — implementar (paralelo, orquestado)
archon workflow run implementar-hu "<id>"
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
archon workflow run implementar-tarea "<id> F<NN>"
# (iterás hasta "ok")
# Modo standalone: commiteá TODO (incluyendo spinal files):
git add <files_created> <files_modified> hubara_agency/.exoclaw/results/<id>/F<NN>-result.yaml
git commit -m "<id> F<NN>: <título>"
git push
```

---

## 6. Lanzar y controlar workflows desde el chat (CLI / Telegram / Web UI)

Además del comando `archon workflow run <nombre>` directo en la terminal, Archon
ofrece **3 caminos adicionales** para arrancar y controlar workflows: el chat
del CLI, el bot de Telegram, y la Web UI. Todos comparten el mismo set de
slash-commands.

### 6.1 Pre-condición para que el chat funcione

Los workflows interactivos (los 4 nuestros lo son) requieren `interactive: true`
**a nivel WORKFLOW**, no sólo dentro del `loop:`. Los 4 archivos ya lo tienen
así — si modificás un workflow asegurate de no perderlo, porque sin eso el
chat no relaya tu feedback a la corrida y la corrida queda colgada.

### 6.2 Archon chat (CLI)

`archon chat` es un comando one-shot que le manda un mensaje al orquestador
de Archon. **No requiere estar dentro de un git repo**. Útil para:
  - Preguntar estado del sistema (`/status`)
  - Disparar workflows por intención natural ("corré refinar-hu con tal HU")
  - Aprobar / cancelar workflows en curso

```bash
# Pregunta libre — el orquestador interpreta y responde
archon chat "qué workflows tengo disponibles?"
archon chat "qué corridas están activas?"

# Slash-commands (idénticos al chat de Telegram / Web)
archon chat "/status"                                # estado del sistema
archon chat "/workflow approve <run-id>"             # aprobar un loop pausado
archon chat "/workflow approve <run-id> aprobada"    # aprobar con comentario
archon chat "/workflow reject <run-id> revisar tags" # rechazar / pedir cambios
archon chat "/workflow cancel <run-id>"              # ABORTAR un subproceso vivo

# Disparar un workflow desde el chat (natural language)
archon chat "lanzá refinar-hu para 'agregar tool de envío de imágenes'"
archon chat "corré planificar-hu con HU-2026-05-11-envio-imagenes"
```

> **Aviso**: el comando `archon chat` corrido DESDE adentro de Claude Code
> tiene el mismo warning de `CLAUDECODE=1` que `archon workflow run`. Si vas
> a chatearle a Archon mientras Claude Code está abierto, hacelo desde una
> terminal externa.

### 6.3 Telegram

`archon doctor` ya reporta `✓ Telegram: getMe OK` en tu setup — el bot está
funcionando. Configurado via `TELEGRAM_BOT_TOKEN` en `~/.archon/.env`. El
nombre del bot está en `BOT_DISPLAY_NAME` de ese mismo `.env`.

**Cómo usarlo**:
  1. Abrí Telegram y conversá con el bot que configuraste.
  2. Mandale mensajes naturales o slash-commands.

**Lo que el bot acepta**:

```
# Disparar un workflow por intención
"refiná técnicamente: quiero que el agente envíe imágenes de productos"
→ El orquestador detecta intención de refinamiento, mapea a `refinar-hu`,
  arranca la corrida, y te empieza a streamear progreso en el chat.

# Slash-commands (mismas que CLI chat)
/status
/workflow approve <run-id>
/workflow reject <run-id> <comentario>
/workflow cancel <run-id>
/help

# Aprobar un loop interactivo por natural language
"aprobada"
"ok, seguí"
"looks good"
→ Si hay UN workflow pausado esperando feedback, auto-detecta y aprueba.
  Si hay varios pausados, te pide que especifiques el run-id.

# Rechazar / iterar con feedback
"no, cambia X por Y"
"agregá un caso para Z"
→ Inyecta el texto como $LOOP_USER_INPUT de la próxima iteración del loop.
```

**Flujo recomendado por Telegram** (modo "comodidad"):

```
Vos:   refiná técnicamente: quiero que el agente envíe imágenes de productos
Bot:   ✅ Arranqué refinar-hu (run abc123). Streameando...
       [progreso del workflow]
       ⏸  Esperando feedback. Revisá el refinamiento. ¿Aprobás o tenés cambios?

Vos:   agregá criterio: el envío debe respetar el rate-limit de WhatsApp
Bot:   📝 Iterando con tu feedback... [progreso]
       ⏸  Acá está la versión 2. ¿OK?

Vos:   ok aprobada
Bot:   ✅ Aprobado. Persistí el refinamiento. HU id: HU-2026-05-11-envio-imagenes
       Comandos git sugeridos:
         git add hubara_agency/.exoclaw/refinements/HU-2026-05-11-envio-imagenes-*.md
         git commit -m "HU-2026-05-11-envio-imagenes: refinamiento aprobado"
         git push
       Próximo paso: `archon workflow run planificar-hu "HU-2026-05-11-envio-imagenes"`
       (o decime "planificá esta HU" y la lanzo)
```

### 6.4 Web UI

`archon serve` levanta una UI local (descarga el bundle la primera vez):

```bash
archon serve                  # default puerto 3090
archon serve --port 8080      # custom
```

Abrí http://localhost:3090 (o el puerto que elijas). La UI te muestra:
  - Lista de workflows disponibles (los 4 nuestros aparecen).
  - Corridas activas con su estado (running, paused, completed, failed).
  - Botón "Cancel" en cada run (equivalente a `/workflow cancel <run-id>`).
  - Dashboard de timing de nodos.

Sirve también como chat alternativo al CLI / Telegram. **Recomendación
oficial de Archon**: corré `archon serve` desde una terminal NORMAL (no
desde adentro de Claude Code) — evita el warning de CLAUDECODE=1 y los
cuelgues silenciosos.

### 6.5 Cuándo conviene cada uno

| Caso | Mejor opción |
|------|--------------|
| Estás en tu compu, terminal abierta, sabés el workflow exacto | `archon workflow run` directo |
| Quiero arrancar una HU sin acordarme del nombre del workflow | `archon chat "refiná: ..."` o Telegram |
| Voy a estar lejos de la compu y quiero seguir aprobando iteraciones | Telegram |
| Quiero ver progreso/timing de varios workflows a la vez | `archon serve` (Web UI) |
| Tengo que cancelar una corrida colgada | `/workflow cancel <run-id>` en CHAT (no existe `archon workflow cancel`) |
| Tengo que reanudar una corrida vieja que falló | `archon workflow resume <run-id>` |
| Quiero estado puntual de todas las corridas | `archon workflow status` |

### 6.6 Lo que el chat / Telegram NO automatiza

Los 4 workflows funcionan vía chat (refinar / planificar / implementar-tarea /
implementar-hu) pero algunas acciones siguen siendo manuales del operador:

  - **Los `git add` / `git commit` / `git push`** después de cada workflow.
    El bot te imprime los comandos exactos pero los corrés vos a mano.
  - **El fan-out de terminales paralelas** en implementar-hu. El bot imprime
    los N comandos `archon workflow run implementar-tarea ...` pero los
    lanzás vos. Cada uno crea su worktree y reporta de vuelta vía resultado
    en `.exoclaw/results/`.

En resumen: el chat es excelente para **decisiones interactivas** (aprobar /
rechazar / iterar) y **arranque de workflows individuales**. Las acciones
git y el fan-out paralelo siguen siendo manuales.

---

## 7. Troubleshooting

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

## 8. Quick reference de archivos canónicos

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
