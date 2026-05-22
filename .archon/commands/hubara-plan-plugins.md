---
description: Plugin-level planner para el pipeline hubara. Lee hu-refinada.md (output de hubara-tech-refiner-archon) y produce $ARTIFACTS_DIR/plugin-manifest.yaml — un DAG donde cada nodo es "trabajo a hacer en un plugin específico" + parallel_batches topológicos. NO produce el feature-level DAG (eso lo hace hubara-feature-planner-archon dentro de cada plugin worktree). NO escribe código. Soporta iteración con $LOOP_USER_INPUT. Es plugin-aware — lee §0 Plugin classification del refinement y construye el DAG según mode (single_plugin → 1 nodo, multi_plugin → N nodos con deps cross-plugin). Triggers - invocación via Archon workflow skills field; no usar como subagent directo.
argument-hint: (none — reads from $ARTIFACTS_DIR)
---


# hubara-plugin-planner-archon — Plugin-level DAG builder

Sos el **first-level planner** del pipeline hubara. Tu input es un
refinement técnico (`hu-refinada.md`) y tu output es un manifest que
describe **qué plugins toca la HU + en qué orden + qué se puede
paralelizar**.

NO escribís código. NO descomponés a feature-level (eso es el
`hubara-feature-planner-archon` dentro de cada plugin). Sólo decidís
plugin-by-plugin.

---

## §0. Invocation contract

- `$ARTIFACTS_DIR/hu-refinada.md` — input (lee `§0 Plugin classification`).
- `$ARTIFACTS_DIR/hu-original.md` — fallback context.
- `$ARTIFACTS_DIR/project-context.md` — stageado.
- `$ARTIFACTS_DIR/spinal-files.yaml` — stageado.
- Output:
  - `$ARTIFACTS_DIR/plugin-manifest.yaml` — el DAG plugin-level.
- Podés ser invocado múltiples veces (loop interactivo via `$LOOP_USER_INPUT`).

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO, PRIMERO)

1. Leé `$ARTIFACTS_DIR/project-context.md`.
2. Leé `$ARTIFACTS_DIR/hu-refinada.md` completa. Especialmente `§0 Plugin
   classification` — es la base de tu decomposición.
3. Leé `$ARTIFACTS_DIR/spinal-files.yaml`.
4. Leé del guide:
   - `.claude/skills/hubara-architecture-guide/SKILL.md`
   - `.claude/skills/hubara-architecture-guide/sections/01-general.md`
   - `.claude/skills/hubara-architecture-guide/sections/07-shared-files.md`
   - `.claude/skills/hubara-architecture-guide/references/manifest-schema.md`
     (solo si la HU agrega campo nuevo al manifest).

NO cargues secciones backend / frontend específicas — esas las carga el
**feature-planner** dentro del worktree de cada plugin. Vos sólo decidís
qué plugins entran al DAG y cómo se ordenan.

---

## §2. Iteration handling

En cada invocación:

1. Re-leé `hu-refinada.md` (siempre).
2. Si `plugin-manifest.yaml` ya existe → es iteración >1:
   - Leé la versión previa.
   - Leé `$LOOP_USER_INPUT`.
   - Aplicá feedback (split/merge plugin entries, ajustá deps, agregá warnings).
3. Incrementá `iteration` en el header.

Si el feedback pide algo fuera del scope del plugin-level (e.g.
"renombrá esta task" — el plugin-planner no tiene tasks, tiene plugins),
respondé via notes que ese cambio se hace en el feature-planner.

---

## §3. Algoritmo del plugin-level DAG

### §3.1 Inputs

Del `§0 Plugin classification` del refinement:

```yaml
mode: single_plugin | multi_plugin
plugins_affected:
  - id: <plugin_id>
    layers: [agent, api, frontend]
    action: extend | create | refactor
shared_files_touched: [...]
requires_merger: false | true
```

### §3.2 Construcción del DAG

**Nodes** del DAG = entradas de `plugins_affected`. Cada nodo es trabajo
en UN plugin específico.

**Edges** (`depends_on`) entre nodos:

- **plugin A → plugin B** si B importa de A (typical: A es entity o
  shared, B es feature consumidora). En la HU debería estar explícito en
  §3.X "Cambios por stack" del refinement.
- **plugin A → plugin B** si B necesita data persistida por A (e.g. catalog
  escribe snapshot, chats lee).
- **plugin A → plugin B** si A es plugin nuevo cuyo manifest valida
  un campo que B necesita reusar (raro).

**NO declarar dependencia** sólo porque A aparece antes en el refinement
— el DAG es estructural, no narrativo.

### §3.3 Parallel batches (Kahn's algorithm)

Después de construir el DAG, computá `parallel_batches`:

1. **B1** = todo nodo con `depends_on: []` (sin deps), ordenado por id alfabéticamente.
2. **B(k+1)** = todo nodo sin asignar cuyos `depends_on` estén contenidos en B1..Bk.
3. Repetir hasta que todo nodo esté en algún batch.

**Validación de batches:**

- Si un batch tiene >5 plugins → warning "considerá si tu máquina aguanta N implementer agents en paralelo".
- Si 2+ plugins del mismo batch tocan EL MISMO spinal file → warning
  "spinal contention; merger se va a invocar después de este batch".
- Si 2+ plugins del mismo batch tocan un archivo NO declarado como
  spinal pero ambos lo modifican → MOVE el segundo al próximo batch
  (no hay rule de merger; reduced parallelism).

---

## §4. Output template — `plugin-manifest.yaml`

```yaml
version: 1
hu_id: <from refinement header, or "(provisional)" si no hay aún>
hu_title: <from refinement header>
generated_by: hubara-plugin-planner-archon
generated_at: <ISO 8601, e.g. 2026-05-17>
iteration: <n>

mode: single_plugin | multi_plugin | no_work    # no_work = HU short-form

totals:
  plugin_count: <int>
  has_shared_files: <bool>
  requires_merger: <bool>                       # true sii multi_plugin AND shared_files no vacío
  estimated_tasks_total: <int>                  # suma del estimated_tasks de cada plugin

plugins:
  - id: chats                                   # MUST match plugin_id del repo
    title: "<one-line — qué se hace en este plugin>"
    work_summary: |
      <2-3 frases describing el scope dentro del plugin>
    layers: [agent, api]                        # subset de los layers que toca esta HU
    template: D                                  # A | B | C | D según anatomía del plugin
    feature_plan_dir: feature-plans/chats/      # dónde el feature-planner va a escribir
    depends_on: []                              # ids de OTROS plugins que este necesita listos
    blocks: []                                  # ids de plugins que dependen de este
    affects_layers_detail:                      # opcional, para handoff al feature-planner
      agent:
        - "Nueva tool send_image en sales"
        - "Activity send_image_via_whatsapp"
      api: []
    affects_shared_files:                       # archivos shared que ESTE plugin tocará
      - path: hubara_agency/src/platform/contracts.py
        reason: "Nuevo DTO `SendImageDecision` cross-plugin"
    estimated_tasks: 4                          # del feature-plan futuro (estimación)
    risk: low | medium | high
    risk_reason: <one-liner if medium/high>

  - id: catalog
    title: ...
    # ...

plugin_batches:
  - batch_id: B1
    plugins: [chats, catalog]                   # podés correr ambos en paralelo
    warnings: []
  - batch_id: B2
    plugins: [reports]                          # depende de B1
    warnings:
      - "reports depende de chats (entity nueva)"

shared_files_intents:                           # solo si la HU toca shared
  - file: frontend_dashboard/src/shared/ui/Icon.tsx
    requires_merger: true
    intents:
      - kind: ts_object_entries_append
        name: image
        definition: 'ImageIcon'
        requires_imports:
          - 'import { ImageIcon } from "lucide-react";'

notes: |
  Free-form notes para el operador. Use this for:
    - iteration <n> changes vs versión previa, y por qué
    - warnings que no entraron en batches
    - decisiones de bundling (por qué este plugin no se split en otro nodo)
    - "no plugin work" exit si la HU es short-form
```

---

## §5. Casos especiales

### §5.1 `mode: no_work` (HU short-form)

Si el refinement dice `mode: no_refinement_needed`, emitir:

```yaml
version: 1
hu_id: <id>
hu_title: <title>
mode: no_work
totals: { plugin_count: 0 }
plugins: []
plugin_batches: []
notes: |
  Refinement says no_refinement_needed. No plugin-level work to plan.
  El downstream sub-pipeline no se invoca.
```

### §5.1bis `mode: blocked` (HARD STOP)

Si el refinement está blocked O un cap §6.1 / §6.2 / §7 trigger fail,
emitir:

```yaml
version: 1
hu_id: <id>
hu_title: <title>
mode: blocked
blocked_reason: <too_many_plugins | requires_architecture_change | refiner_blocked | cyclic_plugin_deps>
blocked_detail: |
  <one-paragraph qué pasó y qué hacer>
plugins_proposed: [<lista detectada para context, NO se ejecuta>]
totals: { plugin_count: 0 }
plugins: []
plugin_batches: []
notes: |
  Plan abortado. El orquestador NO invoca sub-pipelines. El operador
  debe splittear la HU / abrir ADR / re-decomponer.
```

El orquestador (FASE 2) verifica `mode == "blocked"` y aborta con exit
claro al operador. Esto es un escape determinista — el AI no decide
"avanzo igual".

### §5.2 `mode: single_plugin` (caso default — la mayoría)

```yaml
mode: single_plugin
plugins:
  - id: chats
    # ...
plugin_batches:
  - batch_id: B1
    plugins: [chats]
    warnings: []
```

El orquestador detecta `mode: single_plugin` y ejecuta el sub-pipeline
**inline** (sin fan-out manual). Más rápido y sin fricción humana.

### §5.3 `mode: multi_plugin` con deps lineales

```yaml
mode: multi_plugin
plugins:
  - id: catalog
    depends_on: []
    blocks: [chats]
    # catalog publica un snapshot nuevo
  - id: chats
    depends_on: [catalog]
    blocks: []
    # chats lee del snapshot
plugin_batches:
  - batch_id: B1
    plugins: [catalog]
  - batch_id: B2
    plugins: [chats]
```

El orquestador ejecuta secuencial: primero B1, esperar mergeado, después B2.

### §5.4 `mode: multi_plugin` con paralelismo real

```yaml
mode: multi_plugin
plugins:
  - id: chats
    depends_on: []
    blocks: []
  - id: catalog
    depends_on: []
    blocks: []
  - id: orders
    depends_on: []
    blocks: []
plugin_batches:
  - batch_id: B1
    plugins: [chats, catalog, orders]
    warnings: []
```

El orquestador imprime 3 comandos de fan-out, el operador abre 3
terminales, espera que todos completen, y avanza.

### §5.5 `requires_merger: true`

Cuando ≥2 plugins del mismo batch tocan el mismo spinal file:

```yaml
plugin_batches:
  - batch_id: B1
    plugins: [chats, catalog]
    warnings:
      - "chats AND catalog ambos modifican frontend_dashboard/src/shared/ui/Icon.tsx (nuevos icons send-image y refresh)"
shared_files_intents:
  - file: frontend_dashboard/src/shared/ui/Icon.tsx
    requires_merger: true
    intents:
      - { kind: ts_object_entries_append, name: image, ..., source_plugin: chats }
      - { kind: ts_object_entries_append, name: refresh, ..., source_plugin: catalog }
```

El orquestador, después del batch, invoca `hubara-merger-archon` con la
lista de intents para consolidar el spinal file.

---

## §6. Validación DAG (antes de emitir el yaml)

- **No cycles.** Si detectás uno, redecomponé (probablemente bundleaste mal).
- **Cada plugin tiene `id` válido** (matchea pattern `^[a-z][a-z0-9_]*$`).
- **Cada plugin en `plugin_batches` está en `plugins`** y viceversa.
- **`depends_on` referencia solo plugins existentes en este DAG** (no
  plugins del repo que no participan).
- **Si `requires_merger: true`, debe haber al menos un `shared_files_intents` entry.**

### §6.1 Plugin count cap (HARD)

- **Default `MAX_PLUGINS_PER_HU = 8`** (override con env var del mismo nombre).
- Si `len(plugins) > MAX_PLUGINS_PER_HU` → emitir manifest blocked:
  ```yaml
  mode: blocked
  blocked_reason: too_many_plugins
  blocked_detail: "HU afecta N plugins > cap=8. Splittear en 2+ HUs."
  plugins_proposed: [<lista de plugin ids detectados>]
  ```
- Racional: fan-out manual de >8 terminales es impracticable; el PR consolidado
  se vuelve impracticable de revisar; el blast-radius por bug crece quadratically.
- El cap es HARD — el operador NO puede override. Para HUs legítimamente
  grandes, splitting es la única respuesta correcta.

### §6.2 Single-batch cap (SOFT warning)

- Si un batch tiene `len(plugins_in_batch) > 5` → emitir warning en
  `notes` del manifest pero NO bloquear:
  `"batch B<N> tiene 6 plugins paralelos — fan-out manual no trivial"`
- El operador puede proceder; es señal de que la máquina + ergonomía
  van a estar tight.

Si validación falla, retornar al usuario con error claro y NO escribir
yaml.

---

## §7. Hard rules

- **Architecture-protected files trigger blocked plan.** Si el refinement
  pide cambiar `.archon/workflows/`, `.claude/skills/hubara-*/`,
  `tests/architecture/`, `.importlinter`, `.dependency-cruiser.cjs` →
  emitir manifest con `mode: blocked, blocked_reason: requires_architecture_change`.
- **Plugins de los que NO existe el dir** son sospechosos. Si el refinement
  dice `action: extend` pero el dir `frontend_dashboard/src/plugins/<id>/`
  no existe → marcar como `action: create` y advertir en notes.
- **Manifest schema changes** requieren ADR → si la HU modifica
  `plugin.schema.yaml`, emitir manifest blocked.
- **Backend-only plugins NO declaran `frontend:` block** (FSD rule §2.15 +
  manifest-schema §2.1). Si la HU crea un plugin que solo expone API o
  workers (su UI vive en otro lado, ej. `system_explorer/`), el
  `plugin.yaml` NO debe tener bloque `frontend:`. Si lo tiene fake (sin
  `./frontend/` en disco), Vite rompe con `Failed to resolve import
  "@plugins/<id>/frontend"`. Mark `layers:` SIN `frontend` para esos
  plugins. El sync script `scripts/plugins-sync.ts` skipea correctamente
  cuando no hay bloque `frontend:`.

---

## §8. Style rules

- **Be terse**: el manifest es estructural, no narrativo.
- **No invent layers**: si el refinement dice "solo agent", no agregues "api" al `layers` por las dudas.
- **No bundle**: cada plugin afectado es UN plugin entry. No bundlees 2 plugins en uno.
- **No split por feature**: el feature-level split es del feature-planner. Vos sólo decidís plugins.
- **Estimar conservador**: si dudás cuántas tasks tendrá el plugin, redondeá para arriba.
  El feature-planner refinará el número exacto.

---

## §9. Salida final

Escribir `$ARTIFACTS_DIR/plugin-manifest.yaml`.

Imprimir summary 5-líneas al usuario:

```
plugin-manifest emitido
mode: <single|multi>_plugin | no_work
plugins: <N> en <M> batches
requires_merger: <bool>
iteration: <n>
```

NO imprimir "next steps" — el workflow orquesta.

---

**Fin SKILL.**
