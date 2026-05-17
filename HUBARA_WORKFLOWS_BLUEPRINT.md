# Blueprint — Workflows del pipeline Hubara

> **Status:** especificación detallada (2026-05-16). Sin ejecutar.
> **Companion docs:**
> - `HUBARA_PIPELINE_PLAN.md` — plan maestro + decisiones.
> - `HUBARA_SKILL_BLUEPRINT.md` — estructura del skill `hubara-architecture-guide` + 5 skills delgados.

Este documento es el **pseudocódigo** de los 3 workflows YAML que entrega
PR14-PR17. NO es el YAML completo (ese es ~30 KB cada uno); es la
estructura de nodos + invocaciones de skills + lógica clave, suficiente
para que cualquier implementer pueda traducirla a YAML siguiendo el
patrón de `hu-frontend-pipeline.yaml`.

---

## §1. Convenciones de escritura (compartidas con frontend pipeline)

- **Nodos bash deterministas** para todo lo que no requiere razonamiento
  (cp, mkdir, git, npm, gh, parseo de YAML). 0 tokens.
- **Nodos `loop:` con `skills:`** solo para refinamiento, planificación,
  implementación. Son los caros (~$0.01-0.10 por llamada).
- **Nodos `script:` (runtime: bun)** para JSON manipulation no trivial
  (gen-hu-id, build-pr-body).
- **Nodos `cancel:` con `when:`** para fail-fast con mensaje claro.
- **`trigger_rule: all_done`** para nodos best-effort (Project sync) que
  no deben matar el pipeline si fallan.
- **`when:`** sobre output de bash de detección para smart-resume.
- **Naming**: snake_case-with-hyphens (e.g. `check-prereqs`,
  `cargar-refinamiento`, `commit-plan`).

---

## §2. `idea-a-hu-hubara.yaml`

**Tamaño estimado:** ~25 KB / ~500 líneas YAML.

**Diff vs `idea-a-hu-frontend.yaml`:** mínimo. Las diferencias son:
- Label del issue: `hubara-hu` (no `frontend-hu`).
- Workflow disparado en `lanzar-pipeline`: `hu-hubara-pipeline` (no `hu-frontend-pipeline`).
- Prompt del refiner ajustado (mencionar plugins, no FSD-only).

**Estrategia de implementación:** copy-paste literal de `idea-a-hu-frontend.yaml`
y editar las 3 referencias. Validar con un dry-run con HU dummy.

### §2.1 Estructura de nodos

```
FASE 0 - check-prereqs
   └─ cancel-bad-prereqs (when output != OK)
FASE 1 - normalize-input (raw → idea-original.md)
   └─ cancel-bad-input
FASE 2 - refinar-hu-producto (skill nuevo: hubara-tech-refiner-archon-PRODUCT)
   |   Prompt: "Sos un product owner del dashboard AgencyHubara
   |            (plugin system: backend Python DEHA + frontend FSD).
   |            La idea puede involucrar 1 o más plugins; tu HU debe
   |            decir Como/Quiero/Para + AC Given/When/Then + Out of scope."
   |   Diferencia con frontend: NO menciona FSD explícito; sí menciona
   |   "plugins" como concepto.
FASE 3 - validate-hu (mismo formato, mismo grep)
   └─ cancel-bad-hu
FASE 4 - save-draft (a frontend_dashboard/.frontend/drafts/idea-<ts>.md
                     o nuevo hubara_agency/.hubara/drafts/?
                     DECISION: usar hubara_agency/.hubara/drafts/ para no
                     mezclar; ver §4.2)
FASE 5 - crear-issue (label "hubara-hu" en vez de "frontend-hu")
        - agregar-a-project (status "Idea refined")
        - print-issue-info
FASE 6 - gate-lanzar-pipeline (approval gate)
        - lanzar-pipeline (env -u CLAUDECODE archon workflow run hu-hubara-pipeline ...)
        - print-final-summary
```

### §2.2 Decisiones específicas

- **¿`refinar-hu-producto` usa el mismo refiner que `hubara-tech-refiner-archon`?**
  No. El de producto es UNA pasada sin loop (output: HU en formato Como/Quiero/Para).
  El tech-refiner es iterativo con loop (output: refinement técnica con 14 secciones).
  Crear un skill separado: `hubara-product-refiner-archon` (simple, sólo en este workflow).
  O alternativa: NO crear skill separado, inline el prompt en el node `refinar-hu-producto`
  igual que hace `idea-a-hu-frontend.yaml` actualmente. **Recomendación: inline**, evita
  un skill extra.

---

## §3. `hu-hubara-pipeline.yaml`

**Tamaño estimado:** ~80-90 KB / ~1700 líneas YAML (más grande que el frontend
porque tiene 2 ramas: single-plugin auto vs multi-plugin fan-out).

### §3.1 Estructura de nodos (pseudocódigo)

```
═══════════════════════════════════════════════════════════════════════
FASE 0 - Bootstrap
═══════════════════════════════════════════════════════════════════════

- id: check-prereqs
  bash: |
    # ESPEJO DE hu-frontend-pipeline check-prereqs + 2 cosas extra:
    #   - command -v uv (CRÍTICO: pipeline corre uv pytest)
    #   - command -v python3
    #   - hubara_agency/.hubara/spinal-files.yaml debe existir
    #   - hubara_agency/.hubara/project-context.md debe existir
    #   - 5 skills hubara-* en .claude/skills/ deben existir
    #   - pyproject.toml (uv workspace root)
    #   - hubara_agency/uv.lock para `uv sync`
    #   - DIRTY_PROTECTED check ampliado: incluir hubara_agency/tests/architecture/
    #     y hubara_agency/.importlinter
    # Exit OK / FAIL_<reason>
  timeout: 180000

- id: cancel-bad-prereqs
  cancel: |
    # ESPEJO con diagnóstico ampliado para FAIL_* nuevos
  when: $check-prereqs.output != 'OK'

- id: stage-shared-files
  bash: |
    # cp hubara_agency/.hubara/{spinal-files.yaml,project-context.md} → $ARTIFACTS_DIR/
    # cp .archon/github-project-config.yaml → $ARTIFACTS_DIR/ (if exists)
  depends_on: [check-prereqs]

- id: resolve-input
  bash: |
    # IDÉNTICO a hu-frontend-pipeline (acepta URL issue / HU id / local file / plain text)
  depends_on: [stage-shared-files]

- id: cancel-bad-input
- id: gen-hu-id (script: bun, idéntico)
- id: setup-branch (bash, idéntico: branch hu/<HU_ID>, push, fetch del refinement si resume)

- id: detect-resume-state
  bash: |
    # Detecta qué fases ya están committeadas en main o en hu/<HU_ID>:
    #   - already_refined: existe hubara_agency/.hubara/refinements/<HU_ID>-tech.md
    #   - already_planned: existe hubara_agency/.hubara/plans/<HU_ID>/plugin-manifest.yaml
    #   - per-plugin already_implemented: existe hubara_agency/.hubara/results/<HU_ID>/plugin-<id>-result.yaml
    # Output: jq object con flags
  depends_on: [setup-branch]

- id: project-set-refining
  bash: # ESPEJO del frontend, set status "Refining"
  trigger_rule: all_done

═══════════════════════════════════════════════════════════════════════
FASE 1 - Refinar técnico
═══════════════════════════════════════════════════════════════════════

- id: load-refinement-if-resume
  bash: |
    # Si detect-resume-state dice already_refined, cp del repo a $ARTIFACTS_DIR
    # Output: SKIP_REFINER / DO_REFINER
  when: $detect-resume-state.output.already_refined == 'true'

- id: refinar-auto
  depends_on: [load-refinement-if-resume]
  when: $detect-resume-state.output.already_refined != 'true'
  loop:
    max_iterations: 2     # 1 try + 1 retry si validation falla
    until: REFINER_OK
    skills:
      - hubara-tech-refiner-archon
    prompt: |
      Refiná técnicamente la HU según el skill hubara-tech-refiner-archon.

      Input: $ARTIFACTS_DIR/hu-original.md
      Output esperado: $ARTIFACTS_DIR/hu-refinada.md con las 14 secciones +
                       §0 Plugin classification.

      Antes de empezar, cargá el contexto del guide:
        Read .claude/skills/hubara-architecture-guide/sections/01-general.md
        Read .claude/skills/hubara-architecture-guide/sections/07-shared-files.md

      Si la HU pinta backend (mencionas tools, workflows, activities, FastAPI):
        Read .claude/skills/hubara-architecture-guide/sections/02-backend-platform.md
        Read .claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md

      Si la HU pinta frontend (mencionas UI, componentes, dashboards):
        Read .claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md
        Read .claude/skills/hubara-architecture-guide/sections/06-frontend-plugin.md

      Si tu refinement es válido (14 secciones + §0 completo):
        <promise>REFINER_OK</promise>

- id: validate-refinement
  bash: |
    # Grep checks:
    #   - File exists
    #   - Header "§0 Plugin classification" present
    #   - mode: single_plugin|multi_plugin
    #   - plugins_affected: al menos 1
    #   - 14 secciones (## §1 ... ## §14) presentes
    #   - No paths protected en §3 (tests/architecture/, .importlinter, etc.)
    # Output: PASS / FAIL_<reason>
  depends_on: [refinar-auto, load-refinement-if-resume]
  trigger_rule: all_done

- id: cancel-bad-refinement
  cancel: |
    Refinement validation FAILED: $validate-refinement.output
    Para retomar manualmente:
      $EDITOR hubara_agency/.hubara/refinements/<HU_ID>-tech.md
      git add hubara_agency/.hubara/refinements/
      git commit -m "<HU_ID>: refinement manual"
      git push origin hu/<HU_ID>
      archon workflow run hu-hubara-pipeline "<HU_ID>"
  when: $validate-refinement.output != 'PASS'

- id: commit-refinement
  bash: |
    # mkdir -p hubara_agency/.hubara/refinements/
    # cp $ARTIFACTS_DIR/hu-refinada.md → hubara_agency/.hubara/refinements/<HU_ID>-tech.md
    # cp $ARTIFACTS_DIR/hu-original.md → hubara_agency/.hubara/refinements/<HU_ID>-original.md
    # git add + commit + push origin hu/<HU_ID>
  when: $validate-refinement.output == 'PASS'

- id: project-set-refined
  # ESPEJO

═══════════════════════════════════════════════════════════════════════
FASE 2 - Plan plugin-level
═══════════════════════════════════════════════════════════════════════

- id: load-plan-if-resume (idéntico patrón)

- id: planificar-auto
  loop:
    max_iterations: 2
    until: PLANNER_OK
    skills:
      - hubara-plugin-planner-archon
    prompt: |
      Decomponé la HU refinada en un DAG plugin-level.

      Input: $ARTIFACTS_DIR/hu-refinada.md (con §0 plugin classification)
      Output: $ARTIFACTS_DIR/plugin-manifest.yaml

      Antes de empezar, cargá el contexto del guide:
        Read .claude/skills/hubara-architecture-guide/sections/01-general.md
        Read .claude/skills/hubara-architecture-guide/sections/07-shared-files.md
        Read .claude/skills/hubara-architecture-guide/references/manifest-schema.md

      Regla CRÍTICA: si §0 dice multi_plugin, cada plugin tocado es un nodo
      del DAG. Plugins ortogonales (sin deps mutuas) van en el MISMO batch
      (paralelo). Plugins con deps van en batches separados (topológico).

      Si tu plan es válido:
        <promise>PLANNER_OK</promise>

- id: validate-plan
  bash: |
    # Checks:
    #   - plugin-manifest.yaml exists
    #   - plugin_count entre 1 y 8 (cap conservador)
    #   - cada plugin tiene work_summary, layers, template, estimated_tasks
    #   - plugin_batches existe y cubre todos los plugins
    #   - mode == single_plugin sii plugin_count == 1
    # Output: PASS_SINGLE / PASS_MULTI / FAIL_<reason>

- id: cancel-bad-plan (con retomar instructions)

- id: commit-plan
  # mkdir -p hubara_agency/.hubara/plans/<HU_ID>/
  # cp $ARTIFACTS_DIR/plugin-manifest.yaml → ...
  # git add + commit + push

- id: project-set-planned

═══════════════════════════════════════════════════════════════════════
FASE 3 - Implementación (DOS RAMAS: single-plugin vs multi-plugin)
═══════════════════════════════════════════════════════════════════════

# ── RAMA A: single-plugin (auto inline) ─────────────────────────────

- id: project-set-implementing
  depends_on: [commit-plan]
  trigger_rule: all_done

# Pre-warm el venv (igual que frontend)
- id: prewarm-uv-venv
  bash: |
    if [ -d hubara_agency ]; then
      cd hubara_agency && uv sync 2>&1 | tail -20
    fi
  timeout: 600000
  depends_on: [commit-plan]

# Single-plugin: ejecutar el sub-pipeline inline (sin fan-out)
- id: implementar-single-plugin-inline
  when: $validate-plan.output == 'PASS_SINGLE'
  depends_on: [commit-plan, prewarm-uv-venv]
  idle_timeout: 1800000
  loop:
    max_iterations: 30
    until: NEVER_AI_SIGNAL
    fresh_context: true
    skills:
      - hubara-feature-planner-archon
      - hubara-implementer-archon
    until_bash: |
      # ESPEJO del until_bash de frontend pipeline pero con gates dobles:
      #   - npm test (si tarea tocó frontend)
      #   - npm run test:arch
      #   - cd hubara_agency && uv run pytest <tests del plugin> (si tarea tocó backend)
      #   - cd hubara_agency && uv run pytest -m architecture
      #   - cd hubara_agency && uv run lint-imports
      #   - cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml (si tarea tocó manifest)
      #   - playwright E2E con FastAPI background (si tarea tocó UI)
      # Mismo det-retry pattern del frontend.
      # Commit: "${HU_ID} [${plugin_id}] ${TASK_ID}: status=${STATUS} (auto)"
    prompt: |
      ITERACIÓN 1: si NO existe $ARTIFACTS_DIR/feature-plan-manifest.yaml,
      invocá hubara-feature-planner-archon. Va a leer hu-refinada + plugin-manifest
      y emitir feature-plan-manifest.yaml + tareas/F<NN>-*.md.

      ITERACIONES 2+: invocá hubara-implementer-archon sobre la siguiente
      tarea pendiente del feature plan.

      Antes de invocar el implementer, cargá del guide las secciones según
      las layers de la tarea (la tarea las declara en §3 affects_layers):
        - backend: 02 + 03 + 04 + 08
        - frontend: 05 + 06 + 08
        - cross-stack: ambas
        - shared: + 07
      SIEMPRE: 08-tests-and-gates.md.

      Si tu task-result.yaml dice passed, until_bash corre los gates.

# ── RAMA B: multi-plugin (fan-out manual) ─────────────────────────

- id: print-fan-out-commands
  when: $validate-plan.output == 'PASS_MULTI'
  depends_on: [commit-plan]
  bash: |
    HU_ID=$(echo $gen-hu-id.output | jq -r '.hu_id')
    MANIFEST="hubara_agency/.hubara/plans/${HU_ID}/plugin-manifest.yaml"

    echo ""
    echo "═══════════════════════════════════════════════════════════════════"
    echo " HU multi-plugin detectada — fan-out manual"
    echo "═══════════════════════════════════════════════════════════════════"
    echo ""
    echo "El planner identificó N plugins en M batches. Abrí una terminal"
    echo "POR CADA plugin del primer batch y corré:"
    echo ""
    # Parsear plugins del primer batch del manifest y emitir comandos:
    for plugin in $(parse_first_batch_plugins "$MANIFEST"); do
      echo "  archon workflow run hu-hubara-plugin-pipeline \"${HU_ID} ${plugin}\""
    done
    echo ""
    echo "Cada uno va a:"
    echo "  1. Crear su worktree fresh desde origin/hu/${HU_ID}"
    echo "  2. Planificar el feature-level dentro del plugin"
    echo "  3. Implementar las features secuencialmente"
    echo "  4. Commitear + pushear a hu/${HU_ID}"
    echo ""
    echo "Cuando TODOS terminen, volvé acá y respondé 'ready' en el approval."
    echo ""
    echo "Si alguno falla, lo verás en hubara_agency/.hubara/results/${HU_ID}/"
    echo "y el approval te va a mostrar la lista de successful vs failed."
    echo ""

- id: wait-fan-out-done
  when: $validate-plan.output == 'PASS_MULTI'
  depends_on: [print-fan-out-commands]
  approval:
    message: |
      Respondé "ready" cuando TODOS los sub-pipelines hayan terminado
      (sea con success o failure).

      Voy a:
        1. git fetch origin
        2. git merge --ff-only origin/hu/${HU_ID}  (recoge los commits)
        3. Validar que cada plugin tiene plugin-<id>-result.yaml con status: passed
        4. Si algún plugin falló, abortar con el detalle.
        5. Si todos passed, lanzar al siguiente batch (si hay) o pasar a FASE 4.

      Respondé "abort" si querés frenar todo.

- id: merge-fan-out-batch
  when: $validate-plan.output == 'PASS_MULTI'
  depends_on: [wait-fan-out-done]
  bash: |
    HU_ID=$(echo $gen-hu-id.output | jq -r '.hu_id')
    BRANCH="hu/${HU_ID}"

    git fetch origin
    git merge --ff-only "origin/${BRANCH}" || {
      echo "FAIL_FF_MERGE — el branch local divergió. Investigá a mano."
      exit 1
    }

    # Validar plugins del batch actual
    RESULTS_DIR="hubara_agency/.hubara/results/${HU_ID}"
    BATCH_PLUGINS=$(parse_current_batch_plugins "...")
    MISSING=0
    FAILED=0
    for p in $BATCH_PLUGINS; do
      RFILE="${RESULTS_DIR}/plugin-${p}-result.yaml"
      if [ ! -f "$RFILE" ]; then
        echo "MISSING: ${p}"
        MISSING=$((MISSING+1))
      else
        STATUS=$(grep '^status:' "$RFILE" | awk '{print $2}')
        if [ "$STATUS" != "passed" ]; then
          echo "FAILED: ${p} status=$STATUS"
          FAILED=$((FAILED+1))
        fi
      fi
    done
    if [ "$MISSING" -gt 0 ] || [ "$FAILED" -gt 0 ]; then
      echo "FAIL_BATCH_INCOMPLETE missing=${MISSING} failed=${FAILED}"
      exit 1
    fi

    echo "BATCH_OK"

- id: cancel-on-fan-out-failure
  cancel: |
    Batch incompleto. Algunos sub-pipelines no terminaron / fallaron.
    Revisá hubara_agency/.hubara/results/${HU_ID}/ para detalle.
    Para retomar:
      1. Lanzá los sub-pipelines faltantes con `archon workflow run hu-hubara-plugin-pipeline "<HU_ID> <plugin_id>"`
      2. Cuando todos passed, re-lanzá `hu-hubara-pipeline "<HU_ID>"` (smart-resume agarra el batch desde donde quedó)
  when: $merge-fan-out-batch.output != 'BATCH_OK'

# Loop multi-batch (si plan-manifest tiene >1 batch):
# Si hay batch B2, B3..., agregar nodos
# print-fan-out-commands-B2, wait-fan-out-done-B2, merge-fan-out-batch-B2 igual al B1.
# DECISIÓN: el primer corte soporta hasta 3 batches hardcodeados (B1, B2, B3).
# Si la HU necesita >3 batches, planner re-decompose o el operador
# corre por chunks. Ampliable después.

═══════════════════════════════════════════════════════════════════════
FASE 4 - Validación final consolidada
═══════════════════════════════════════════════════════════════════════

- id: check-pipeline-error
  bash: |
    # ESPEJO del frontend pero también revisa pipeline-error.yaml producidos
    # por cualquier sub-pipeline.
  depends_on: [implementar-single-plugin-inline, merge-fan-out-batch]
  trigger_rule: all_done

- id: cancel-on-implement-error (ESPEJO con instructions ampliadas)

- id: final-validation
  bash: |
    # CRITICAL: pipefail
    set -o pipefail

    echo "=== render-compose check ===" >&2
    cd hubara_agency && uv run python scripts/render-compose.py 2>&1 | tail -5 >&2
    git diff --exit-code hubara_agency/docker-compose.local.yml || {
      echo "FAIL_RENDER_COMPOSE_DRIFT"; exit 0;
    }
    cd ..

    echo "=== uv pytest (full + architecture) ===" >&2
    cd hubara_agency && uv run pytest 2>&1 | tail -30 >&2 || { echo "FAIL_PYTEST"; exit 0; }
    cd hubara_agency && uv run pytest -m architecture 2>&1 | tail -20 >&2 || { echo "FAIL_ARCH"; exit 0; }
    cd ..

    echo "=== uv lint-imports (R-DIP) ===" >&2
    cd hubara_agency && uv run lint-imports 2>&1 | tail -10 >&2 || { echo "FAIL_LINT_IMPORTS"; exit 0; }
    cd ..

    echo "=== npm test ===" >&2
    cd frontend_dashboard && npm test 2>&1 | tail -30 >&2 || { echo "FAIL_NPM_TEST"; exit 0; }
    cd ..

    echo "=== npm test:arch ===" >&2
    cd frontend_dashboard && npm run test:arch 2>&1 | tail -20 >&2 || { echo "FAIL_NPM_ARCH"; exit 0; }
    cd ..

    echo "=== npx tsc -b ===" >&2
    cd frontend_dashboard && npx tsc -b 2>&1 | tail -30 >&2 || { echo "FAIL_TSC"; exit 0; }
    cd ..

    echo "=== npm run build ===" >&2
    cd frontend_dashboard && npm run build 2>&1 | tail -30 >&2 || { echo "FAIL_BUILD"; exit 0; }
    cd ..

    # Playwright E2E final con FastAPI background — ESPEJO del frontend
    # En realidad ya corre dentro del until_bash de cada sub-pipeline, pero
    # acá lo re-corremos contra el branch consolidado para tener evidencia
    # del estado final. Random port + cleanup, copy-paste del frontend.

    echo "PASS"
  timeout: 1200000  # 20 min (es la suite full)
  when: $check-pipeline-error.output == 'OK'

- id: cancel-on-final-validation-fail (ESPEJO)

═══════════════════════════════════════════════════════════════════════
FASE 5 - PR
═══════════════════════════════════════════════════════════════════════

- id: build-pr-body
  script: bun
  # Generar pr-body.md leyendo:
  #   - hubara_agency/.hubara/refinements/<HU_ID>-original.md (primeras 20 líneas como summary)
  #   - hubara_agency/.hubara/plans/<HU_ID>/plugin-manifest.yaml (tabla de plugins tocados)
  #   - hubara_agency/.hubara/results/<HU_ID>/*.yaml (tabla de tasks pasadas)
  #   - $ARTIFACTS_DIR/playwright-evidence-*.log (evidencia E2E)
  # Output: $ARTIFACTS_DIR/pr-body.md

- id: create-pr
  bash: |
    HU_ID=$(echo $gen-hu-id.output | jq -r '.hu_id')
    TITLE=$(echo $gen-hu-id.output | jq -r '.title')
    gh pr create \
      --title "${HU_ID}: ${TITLE}" \
      --body-file $ARTIFACTS_DIR/pr-body.md \
      --base main \
      --head hu/${HU_ID} \
      > $ARTIFACTS_DIR/pr-url.txt 2>&1
    PR_URL=$(cat $ARTIFACTS_DIR/pr-url.txt | grep -oE 'https://github\.com/.+/pull/[0-9]+')
    echo "$PR_URL"

- id: project-set-done

═══════════════════════════════════════════════════════════════════════
FASE 6 - Review (V2)
═══════════════════════════════════════════════════════════════════════

- id: trigger-review
  bash: |
    # env -u CLAUDECODE para evitar nested hang
    (env -u CLAUDECODE nohup archon workflow run review-pr-hubara "$(cat $ARTIFACTS_DIR/pr-url.txt)" \
       > $HOME/.archon/logs/review-$(date +%s).log 2>&1 & disown) 2>/dev/null

- id: print-final-summary
  # Tabla con plugins tocados, tasks pasadas, PR URL, review status
```

### §3.2 Manejo de errores específicos del pipeline hubara

| Síntoma | Causa probable | Acción del pipeline |
|---|---|---|
| `final-validation FAIL_RENDER_COMPOSE_DRIFT` | un sub-pipeline tocó manifest pero no commitó docker-compose actualizado | abortar + instruccionar `cd hubara_agency && uv run python scripts/render-compose.py && git add ... && git commit && git push` |
| `final-validation FAIL_LINT_IMPORTS` | violación de R-DIP introducida | abortar + listar contratos violados desde el output de lint-imports |
| `final-validation FAIL_ARCH` | violación de R-rules (R-JSON / R-HEARTBEAT / etc.) | abortar + sugerir re-lanzar el sub-pipeline del plugin culpable |
| Sub-pipeline crashea con git push race | dos sub-pipelines pushean simultáneamente | sub-pipeline tiene retry pull-rebase (mismo patrón frontend); si igual falla, operador hace pull manual |
| Plugin tocado en HU pero no aparece en plugin-manifest | planner se confundió | rechazar el plan con feedback "missing plugin <id>" y re-lanzar planner |

---

## §4. `hu-hubara-plugin-pipeline.yaml`

**Tamaño estimado:** ~50 KB / ~1100 líneas YAML.

Es esencialmente una **versión reducida de `hu-frontend-pipeline.yaml`**
con scope limitado a un plugin específico:
- No tiene fan-out (es el sub-pipeline).
- No crea PR (lo hace el orquestador).
- Pushea al branch `hu/<HU_ID>` que ya existe (lo creó el orquestador).
- Sus gates corren contra el subset del plugin pero respetan el architecture
  gate global (porque eso es lo único que protege la integridad).

### §4.1 Estructura de nodos (pseudocódigo)

```
═══════════════════════════════════════════════════════════════════════
FASE 0 - Bootstrap (worktree fresh + checkout del branch del orq)
═══════════════════════════════════════════════════════════════════════

- id: parse-input
  bash: |
    # $USER_MESSAGE = "<HU_ID> <plugin_id>"
    HU_ID=$(echo "$ARGUMENTS" | awk '{print $1}')
    PLUGIN_ID=$(echo "$ARGUMENTS" | awk '{print $2}')
    [ -n "$HU_ID" ] && [ -n "$PLUGIN_ID" ] || { echo "FAIL_INPUT"; exit 0; }
    jq -n --arg h "$HU_ID" --arg p "$PLUGIN_ID" '{hu_id: $h, plugin_id: $p}'

- id: cancel-bad-input

- id: check-prereqs (ESPEJO reducido del orquestador)
- id: cancel-bad-prereqs

- id: checkout-branch
  bash: |
    HU_ID=$(echo $parse-input.output | jq -r '.hu_id')
    BRANCH="hu/${HU_ID}"
    git fetch origin
    git checkout -b "$BRANCH" "origin/$BRANCH" || git checkout "$BRANCH"
    git pull --ff-only origin "$BRANCH"

- id: stage-plugin-context
  bash: |
    # cp:
    #   hubara_agency/.hubara/spinal-files.yaml → $ARTIFACTS_DIR/
    #   hubara_agency/.hubara/project-context.md → $ARTIFACTS_DIR/
    #   hubara_agency/.hubara/refinements/<HU_ID>-tech.md → $ARTIFACTS_DIR/hu-refinada.md
    #   hubara_agency/.hubara/plans/<HU_ID>/plugin-manifest.yaml → $ARTIFACTS_DIR/
    # Extraer el plugin entry del manifest y guardarlo:
    #   $ARTIFACTS_DIR/plugin-work.yaml (solo el slice del plugin_id de este pipeline)

- id: detect-resume-state-plugin
  bash: |
    # Detecta si ya hay feature-plan / partial results para este plugin
    # Output: already_planned_feature_level | partial_results | fresh

═══════════════════════════════════════════════════════════════════════
FASE 1 - Plan feature-level dentro del plugin
═══════════════════════════════════════════════════════════════════════

- id: planificar-feature-auto
  loop:
    max_iterations: 2
    until: FEATURE_PLANNER_OK
    skills:
      - hubara-feature-planner-archon
    prompt: |
      Decomponé el trabajo del plugin "$PLUGIN_ID" en un DAG feature-level.

      Inputs:
        - $ARTIFACTS_DIR/hu-refinada.md (refinement completo, con §0)
        - $ARTIFACTS_DIR/plugin-manifest.yaml (plan plugin-level)
        - $ARTIFACTS_DIR/plugin-work.yaml (solo el slice de tu plugin)

      Output: $ARTIFACTS_DIR/feature-plan-manifest.yaml +
              $ARTIFACTS_DIR/tareas/F<NN>-*.md

      Cargá del guide:
        Read .claude/skills/hubara-architecture-guide/sections/01-general.md
        Read .claude/skills/hubara-architecture-guide/sections/10-cookbook.md

      Según los layers que toca tu plugin:
        Read .claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md
        Read .claude/skills/hubara-architecture-guide/sections/04-backend-agents.md
        Read .claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md
        Read .claude/skills/hubara-architecture-guide/sections/06-frontend-plugin.md

      Regla: dentro de un plugin, las features están MUCHO menos paralelizables
      que entre plugins (porque comparten worker.py, composition.py, etc.).
      Defaultá a 1 tarea por batch a menos que las features sean explícitamente
      ortogonales.

      Si tu plan es válido:
        <promise>FEATURE_PLANNER_OK</promise>

- id: validate-feature-plan
- id: cancel-bad-feature-plan
- id: commit-feature-plan
  # Persiste a hubara_agency/.hubara/plans/<HU_ID>/feature-plans/<plugin_id>/
  # git add + commit + push origin hu/<HU_ID>

═══════════════════════════════════════════════════════════════════════
FASE 2 - Implementar secuencial
═══════════════════════════════════════════════════════════════════════

- id: prewarm-uv-venv (ESPEJO)

- id: implementar-secuencial
  loop:
    max_iterations: 30
    until: NEVER_AI_SIGNAL
    fresh_context: true
    skills:
      - hubara-implementer-archon
    until_bash: |
      # ESPEJO COMPLETO del until_bash de hu-frontend-pipeline pero con:
      #
      # Gates aplicables según affects_layers de la tarea:
      #   - backend: cd hubara_agency && uv run pytest <tests-del-plugin> -m architecture
      #              cd hubara_agency && uv run lint-imports
      #              cd hubara_agency && uv run python scripts/render-compose.py && check diff (si tocó manifest)
      #   - frontend: cd frontend_dashboard && npm test ...
      #               cd frontend_dashboard && npm run test:arch
      #               cd frontend_dashboard && npx tsc -b
      #               cd frontend_dashboard && npm run build
      #   - functional: cd hubara_agency && uv run pytest tests/functional/test_<feature>.py
      #   - e2e: playwright con FastAPI background (igual que frontend)
      #
      # Det-retry: 2 attempts antes de permanent failure.
      # Transient-retry: 1 attempt para timeout/regression.
      #
      # Commit message:
      #   "${HU_ID} [${PLUGIN_ID}] ${TASK_ID}: status=${STATUS} (auto)"
      #
      # Push retry con pull --rebase (concurrency safety).
      #
      # ALL DONE check: TODO_TASKS de feature-plan-manifest == PASSED_TASKS

    prompt: |
      ITERACIÓN AI sobre una tarea del feature plan.

      Setup:
        HU_ID=$(echo '$parse-input.output' | jq -r '.hu_id')
        PLUGIN_ID=$(echo '$parse-input.output' | jq -r '.plugin_id')
        MANIFEST="hubara_agency/.hubara/plans/${HU_ID}/feature-plans/${PLUGIN_ID}/feature-plan-manifest.yaml"
        RESULTS_DIR="hubara_agency/.hubara/results/${HU_ID}/feature-results/${PLUGIN_ID}"

      Paso A0: Si existe $ARTIFACTS_DIR/test-failures.md, leelo (gate determinista
      falló en iteración previa; aplicá fixes).

      Paso A: Encontrar la próxima tarea pendiente del feature plan.

      Paso B: Cargar inputs:
        cp "$MANIFEST" "$ARTIFACTS_DIR/plan-manifest.yaml"
        cp "tareas/${TASK_ID}-*.md" "$ARTIFACTS_DIR/task.md"
        (refinement ya está cargado desde FASE 0)

      Paso C: Cargar del guide según affects_layers de task.md:
        - backend: 02 + 03 + 04 + 08
        - frontend: 05 + 06 + 08
        - cross-stack: 02 + 03 + 04 + 05 + 06 + 08
        - shared: + 07

      Paso D: Aplicar protocolo del skill hubara-implementer-archon:
        - escribir código + tests + functional/e2e tests
        - correr §10 verification commands
        - escribir $ARTIFACTS_DIR/task-result.yaml

      Paso E: NO hagas git. until_bash se encarga.

═══════════════════════════════════════════════════════════════════════
FASE 3 - Reporte de retorno al orquestador
═══════════════════════════════════════════════════════════════════════

- id: write-plugin-result
  bash: |
    HU_ID=$(echo $parse-input.output | jq -r '.hu_id')
    PLUGIN_ID=$(echo $parse-input.output | jq -r '.plugin_id')
    RESULTS_DIR="hubara_agency/.hubara/results/${HU_ID}"
    PLUGIN_RFILE="${RESULTS_DIR}/plugin-${PLUGIN_ID}-result.yaml"
    FEATURE_RDIR="${RESULTS_DIR}/feature-results/${PLUGIN_ID}"

    mkdir -p "$RESULTS_DIR"

    # Agregar status global del plugin desde los feature-results
    ALL_PASSED=true
    FAILED_TASKS=()
    for f in "$FEATURE_RDIR"/F*-result.yaml; do
      [ -f "$f" ] || continue
      S=$(grep '^status:' "$f" | awk '{print $2}')
      [ "$S" = "passed" ] || { ALL_PASSED=false; FAILED_TASKS+=("$(basename $f)"); }
    done

    cat > "$PLUGIN_RFILE" <<EOF
    version: 1
    hu_id: ${HU_ID}
    plugin_id: ${PLUGIN_ID}
    pipeline: hu-hubara-plugin-pipeline
    date: $(date -u +%Y-%m-%dT%H:%M:%SZ)
    status: $([ "$ALL_PASSED" = "true" ] && echo "passed" || echo "failed")
    feature_tasks_total: $(ls "$FEATURE_RDIR" 2>/dev/null | wc -l | tr -d ' ')
    feature_tasks_passed: $(grep -l '^status: passed' "$FEATURE_RDIR"/*.yaml 2>/dev/null | wc -l | tr -d ' ')
    failed_tasks:
    EOF
    for t in "${FAILED_TASKS[@]}"; do
      echo "  - $t" >> "$PLUGIN_RFILE"
    done

    git add "$RESULTS_DIR/"
    git commit -m "${HU_ID} [${PLUGIN_ID}]: plugin result.yaml"
    git push origin "hu/${HU_ID}"

- id: print-summary
  bash: |
    # Imprimir tabla con tareas pasadas/fallidas para que el operador sepa
    # qué reportar al orquestador.
```

### §4.2 Atomic safety en el sub-pipeline

El sub-pipeline tiene **dos invariants críticos**:

1. **Nunca toca archivos fuera del plugin assigned.** Hard-rule en el
   prompt del implementer:
   ```
   PROHIBIDO modificar:
     - hubara_agency/src/platform/*  (excepto si tu plugin lo declaró como spinal)
     - hubara_agency/src/plugins/<otro>/*  (cross-plugin = bug)
     - frontend_dashboard/src/plugins/<otro>/*
     - frontend_dashboard/src/shared/* (excepto si tu plugin lo declaró como spinal)
     - frontend_dashboard/src/entities/* (excepto si tu plugin lo declaró como spinal)
   Si tu task necesita tocar uno de estos, marcá `status: blocked,
   blocked_reason: requires_merger`.
   ```

2. **Pushes con pull-rebase retry** (igual que frontend) para sobrevivir
   races entre sub-pipelines paralelos.

---

## §5. Convenciones específicas del pipeline hubara

### §5.1 Persistencia de artifacts (file layout)

```
hubara_agency/.hubara/
├── spinal-files.yaml                        # setup manual, 1 vez
├── project-context.md                       # setup manual, 1 vez
├── drafts/                                  # de idea-a-hu-hubara
│   └── idea-<ts>.md
├── refinements/                             # de hubara-tech-refiner-archon
│   ├── <HU_ID>-tech.md
│   └── <HU_ID>-original.md
├── plans/
│   └── <HU_ID>/
│       ├── plugin-manifest.yaml             # plan plugin-level (orq)
│       └── feature-plans/
│           └── <plugin_id>/
│               ├── feature-plan-manifest.yaml  # plan feature-level (sub-pipeline)
│               └── tareas/F<NN>-*.md
└── results/
    └── <HU_ID>/
        ├── plugin-<plugin_id>-result.yaml   # status global del plugin
        └── feature-results/
            └── <plugin_id>/F<NN>-result.yaml
```

### §5.2 Commit message convention

| Pipeline | Mensaje |
|---|---|
| Orquestador (FASE 1) | `<HU_ID>: refinement (auto)` |
| Orquestador (FASE 2) | `<HU_ID>: plugin-level plan (auto, <N> plugins)` |
| Sub-pipeline (FASE 1) | `<HU_ID> [<plugin_id>]: feature plan (auto, <N> tareas)` |
| Sub-pipeline (FASE 2 — por tarea) | `<HU_ID> [<plugin_id>] <TASK_ID>: status=<STATUS> (auto)` |
| Sub-pipeline (FASE 3) | `<HU_ID> [<plugin_id>]: plugin result.yaml` |

### §5.3 GitHub Project sync

Reusa `.archon/github-project-config.yaml` existente. Status options
sugeridos (agregar al config):
- `Idea refined` (de `idea-a-hu-hubara`)
- `Refining` (FASE 1)
- `Refined` (post FASE 1)
- `Planning` (FASE 2)
- `Planned` (post FASE 2)
- `Implementing` (FASE 3)
- `Reviewing` (FASE 6)
- `Done — PR ready` (FASE 5 success)
- `Blocked` (cualquier failure)

---

## §6. Modos de uso del pipeline

### §6.1 Modo A — Pipeline auto E2E (caso default)

```bash
archon workflow run idea-a-hu-hubara "quiero que el agente pueda enviar imágenes"
# → crea Issue + card "Idea refined" + approval gate
# → si aprobás: archon workflow run hu-hubara-pipeline "<issue-url>" (background)

# Si la HU es single-plugin: termina solo en ~20 min.
# Si la HU es multi-plugin: hace el plan, te imprime N comandos, te pausa.
```

### §6.2 Modo B — Plugin standalone (debug / fix específico)

```bash
archon workflow run hu-hubara-plugin-pipeline "<HU_ID> chats"
# → crea worktree fresh, checkout branch, corre el sub-pipeline del plugin solo
```

### §6.3 Modo C — Resume después de un crash

```bash
# Si el orquestador falla en FASE 4, los sub-pipelines ya commitearon todo.
# Re-lanzar el orquestador: smart-resume detecta lo que ya está y salta.
archon workflow run hu-hubara-pipeline "<HU_ID>"
```

### §6.4 Modo D — Override manual de una fase

```bash
# Si no te gusta el refinement auto, editá a mano:
$EDITOR hubara_agency/.hubara/refinements/<HU_ID>-tech.md
git add hubara_agency/.hubara/refinements/
git commit -m "<HU_ID>: refinement manual override"
git push origin hu/<HU_ID>
# Re-lanzar pipeline: smart-resume salta FASE 1 (refinement ya existe).
archon workflow run hu-hubara-pipeline "<HU_ID>"
```

---

## §7. Tests del pipeline propio (CI-style validation)

Como los workflows son YAML, no tienen unit tests. Pero podemos validar
con un test integration:

```bash
# Test 1: HU dummy single-plugin
archon workflow run idea-a-hu-hubara "agregá un endpoint /healthcheck al plugin chats"
# Esperar approval → aprobar → esperar pipeline ~15 min → ver PR creado.
# Validar: PR existe, contains endpoint, npm test green, uv pytest green.

# Test 2: HU dummy multi-plugin
archon workflow run idea-a-hu-hubara "agregá un dashboard widget que muestre métricas del catálogo y de los chats"
# Esperar approval → aprobar → ver mensaje "abrí 2 terminales"
# En 2 terminales: archon workflow run hu-hubara-plugin-pipeline "<HU_ID> chats"
#                  archon workflow run hu-hubara-plugin-pipeline "<HU_ID> catalog"
# Esperar ambos terminen → volver al orquestador → responder "ready"
# Esperar FASE 4-5 → ver PR creado con commits de ambos plugins.

# Test 3: HU que toca shared file (debería bloquearse en V1)
archon workflow run idea-a-hu-hubara "agregá un icono nuevo 'compass' al sistema"
# Refinement debería marcar requires_merger: true
# Plan debería emitir warning + bloquear la tarea con requires_merger
# Operador resuelve a mano + re-lanza
```

Estos tests son ~3-4 horas de runtime cada uno → no se corren en CI; se
corren manualmente al final de PR17 antes de mergear.

---

---

## §8. `review-pr-hubara.yaml` (V1, resuelto §8 decisión 2 del PLAN)

**Tamaño estimado:** ~30 KB / ~700 líneas YAML.

Es el **espejo directo de `review-pr-frontend.yaml`** adaptado al dominio
hubara (5 agentes especializados en plugin system + DEHA + FSD juntos en
vez de solo FSD).

### §8.1 Estructura de nodos

```
═══════════════════════════════════════════════════════════════════════
FASE 0 - Bootstrap (fetch PR + checkout + diff)
═══════════════════════════════════════════════════════════════════════

- id: parse-input
  bash: |
    # $USER_MESSAGE = "https://github.com/<owner>/<repo>/pull/<N>"
    PR_URL="$ARGUMENTS"
    [[ "$PR_URL" =~ ^https://github\.com/.+/pull/[0-9]+$ ]] || { echo "FAIL_BAD_URL"; exit 0; }
    PR_NUM=$(echo "$PR_URL" | grep -oE '[0-9]+$')
    echo "$PR_URL" > $ARTIFACTS_DIR/pr-url.txt
    echo "$PR_NUM" > $ARTIFACTS_DIR/pr-num.txt

- id: fetch-pr
  bash: |
    PR_NUM=$(cat $ARTIFACTS_DIR/pr-num.txt)
    gh pr view "$PR_NUM" --json title,body,baseRefName,headRefName,files \
      > $ARTIFACTS_DIR/pr.json
    BRANCH=$(jq -r '.headRefName' $ARTIFACTS_DIR/pr.json)
    git fetch origin "$BRANCH"
    git checkout "$BRANCH"
    git pull --ff-only origin "$BRANCH"

- id: fetch-diff
  bash: |
    # Diff vs main, agrupado por archivo para que los agentes puedan grep
    BASE=$(jq -r '.baseRefName' $ARTIFACTS_DIR/pr.json)
    git diff "origin/${BASE}...HEAD" > $ARTIFACTS_DIR/diff.patch
    git diff --name-only "origin/${BASE}...HEAD" > $ARTIFACTS_DIR/files-changed.txt

═══════════════════════════════════════════════════════════════════════
FASE 1 - Classify (haiku decide qué agentes correr)
═══════════════════════════════════════════════════════════════════════

- id: classify
  loop:
    max_iterations: 1
    until: CLASSIFY_OK
    skills: []   # inline prompt, no skill separado
  prompt: |
    Sos un classifier rápido. Leé $ARTIFACTS_DIR/files-changed.txt y decidí
    qué subset de los 5 agentes correr.

    Reglas:
    - agent-deha-compliance: corré si CUALQUIER archivo bajo hubara_agency/src/
    - agent-fsd-compliance: corré si CUALQUIER archivo bajo frontend_dashboard/src/
    - agent-plugin-system: corré si TOCA plugin.yaml, plugin.schema.yaml, plugin_manifest.py, o k8s/aws-produccion/
    - agent-test-coverage: corré SIEMPRE (cubre ambos lados)
    - agent-security: corré si TOCA .env*, secrets/, configmap.yaml, o cualquier archivo con `os.environ` agregado

    Output: JSON con flags { deha: bool, fsd: bool, plugin_system: bool, test_coverage: true, security: bool }.
    Escribilo a $ARTIFACTS_DIR/agents-to-run.json.

    Cuando termines: <promise>CLASSIFY_OK</promise>
  provider: claude
  model: haiku  # classifier barato

═══════════════════════════════════════════════════════════════════════
FASE 2 - 5 agentes en paralelo (cada uno con `when:` sobre classify output)
═══════════════════════════════════════════════════════════════════════

- id: agent-deha-compliance
  when: $classify.output.deha == true
  loop:
    max_iterations: 1
    until: REPORT_OK
    skills: []
  prompt: |
    Sos un experto en DEHA (Durable Execution Hexagonal Architecture).
    Revisá los cambios del PR.

    Cargá del guide:
      Read .claude/skills/hubara-architecture-guide/sections/02-backend-platform.md
      Read .claude/skills/hubara-architecture-guide/sections/03-backend-plugin.md
      Read .claude/skills/hubara-architecture-guide/sections/04-backend-agents.md
      Read .claude/skills/hubara-architecture-guide/references/deha-rules.md

    Inputs:
      - $ARTIFACTS_DIR/diff.patch
      - $ARTIFACTS_DIR/files-changed.txt

    Buscá:
      - R-DET violations: datetime.now() / random / I/O en workflows/
      - R-JSON violations: dataclasses no-frozen cruzando boundary
      - R-STATELESS violations: module-level cache en activities
      - R-HEARTBEAT violations: activities >10s sin @with_heartbeat
      - R-DIP violations: platform/ importing plugins/, o cross-plugin imports
      - Plugin manifest hygiene: workers sin task_queue, drift docker-compose

    Output: $ARTIFACTS_DIR/findings-deha.yaml con:
      version: 1
      agent: deha-compliance
      findings:
        - severity: critical|high|medium|low
          file: <path>
          line: <int>
          rule: R-DET|R-JSON|R-STATELESS|R-HEARTBEAT|R-DIP|plugin-manifest
          message: <one-line>
          fix_suggestion: <patch if obvious>

    Cuando termines: <promise>REPORT_OK</promise>

- id: agent-fsd-compliance
  when: $classify.output.fsd == true
  # ESPEJO PATRÓN del frontend review-pr existente, con
  # Read .claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md
  # Read .claude/skills/hubara-architecture-guide/sections/06-frontend-plugin.md
  # Read .claude/skills/hubara-architecture-guide/references/fsd-rules.md
  # Output: $ARTIFACTS_DIR/findings-fsd.yaml

- id: agent-plugin-system
  when: $classify.output.plugin_system == true
  # Lee 07-shared-files + references/manifest-schema + sections/08
  # Verifica:
  #   - plugin.yaml valida contra schema
  #   - workers declarados tienen K8s manifest (premortem invariant)
  #   - task_queue es unique cross-plugin
  #   - render-compose no tiene drift
  # Output: $ARTIFACTS_DIR/findings-plugin-system.yaml

- id: agent-test-coverage
  when: true  # SIEMPRE
  # Lee 08-tests-and-gates
  # Verifica:
  #   - cada feature nueva en tests/functional/ o e2e/
  #   - architecture gate passing
  #   - import-linter passing
  # Output: $ARTIFACTS_DIR/findings-test-coverage.yaml

- id: agent-security
  when: $classify.output.security == true
  # Verifica:
  #   - no secrets hardcoded (regex AWS keys, GitHub tokens, etc.)
  #   - env vars nuevos están en wiring_intents.env_vars_required del manifest
  #   - no os.environ en código que cruza boundary
  # Output: $ARTIFACTS_DIR/findings-security.yaml

═══════════════════════════════════════════════════════════════════════
FASE 3 - Synthesize (consolida findings + decide auto-fix)
═══════════════════════════════════════════════════════════════════════

- id: synthesize
  loop:
    max_iterations: 1
    until: SYNTH_OK
  prompt: |
    Leé todos los findings-*.yaml de $ARTIFACTS_DIR/. Consolidalos en:

    $ARTIFACTS_DIR/review-report.md
      ## Resumen
      - N findings totales (X critical / Y high / Z medium / W low)
      - Auto-fix attempted: <list>
      - Auto-fix succeeded: <list>
      - Auto-fix reverted (rompió tests): <list>
      - Pendientes (no auto-fixable): <list con file:line + recomendación>

    $ARTIFACTS_DIR/auto-fix-plan.yaml (solo CRITICAL y HIGH):
      fixes:
        - file: <path>
          line: <int>
          patch: <unified diff>
          rule_violated: <R-DET|R-JSON|...>
          revertible_by: <test name>

    Cuando termines: <promise>SYNTH_OK</promise>

═══════════════════════════════════════════════════════════════════════
FASE 4 - Auto-fix CRITICAL/HIGH (revierte si rompe tests)
═══════════════════════════════════════════════════════════════════════

- id: auto-fix
  bash: |
    # Aplicar los patches uno por uno
    # Después de cada uno, correr el test correspondiente del revertible_by
    # Si pasa: keep. Si rompe: git checkout HEAD -- <file>
    # Output: $ARTIFACTS_DIR/fixes-applied.yaml + $ARTIFACTS_DIR/fixes-reverted.yaml

- id: commit-fixes
  bash: |
    # git add + commit + push (con pull --rebase retry)
    # Solo si hay fixes que sobrevivieron tests
    git status --short
    if ! git diff --quiet HEAD; then
      git commit -m "review-pr-hubara: auto-fix critical/high findings"
      git push origin "$(git branch --show-current)" || {
        git pull --rebase origin "$(git branch --show-current)"
        git push origin "$(git branch --show-current)"
      }
    fi

═══════════════════════════════════════════════════════════════════════
FASE 5 - Post comment al PR
═══════════════════════════════════════════════════════════════════════

- id: post-comment
  bash: |
    PR_URL=$(cat $ARTIFACTS_DIR/pr-url.txt)
    gh pr comment "$PR_URL" --body-file $ARTIFACTS_DIR/review-report.md
```

### §8.2 Decisiones de diseño del review

- **Haiku para classify, sonnet para agentes:** el classifier toma <2s y
  es decisión simple; los agentes hacen razonamiento real sobre el código.
- **5 archivos `findings-*.yaml` separados:** facilita debug (si un agente
  falla, los otros 4 siguen) y permite paralelismo real (Archon corre
  agents marcados con `when: true` en paralelo).
- **Auto-fix conservador:** solo CRITICAL + HIGH, y revierte si rompe
  cualquier test. Lo que no se auto-fix queda como TODO en el comment del PR.
- **No bloquea el PR:** el review es informativo. Si hay findings críticos
  sin auto-fix, el operador decide si mergear o iterar.

---

## §9. PR19 — Plan de deprecación de pipelines exoclaw + frontend (resuelto §8 decisión 5 del PLAN)

> Este PR NO va en el scope V1 inicial. Se ejecuta después de validar que
> el pipeline hubara cubre todos los casos en producción (criterio: 3+ HUs
> reales mergeadas exitosamente sin fallback a los pipelines legacy).

### §9.1 Archivos a eliminar

```
.archon/workflows/
├── README.md                          # deprecate (mover historia a ARCHITECTURE.md §13)
├── README-frontend.md                 # idem
├── refinar-hu.yaml                    # DELETE (reemplaza hubara-tech-refiner-archon)
├── planificar-hu.yaml                 # DELETE (reemplaza hubara-plugin-planner-archon)
├── implementar-tarea.yaml             # DELETE (reemplaza hubara-implementer-archon)
├── implementar-hu.yaml                # DELETE (reemplaza hu-hubara-pipeline)
├── idea-a-hu-frontend.yaml            # DELETE (reemplaza idea-a-hu-hubara)
├── hu-frontend-pipeline.yaml          # DELETE (reemplaza hu-hubara-pipeline)
└── review-pr-frontend.yaml            # DELETE (reemplaza review-pr-hubara)

.claude/skills/
├── exoclaw-implementer-archon/        # DELETE
├── exoclaw-merger-archon/              # DELETE
├── exoclaw-task-planner-archon/        # DELETE
├── exoclaw-tech-refiner-archon/        # DELETE
├── frontend-implementer-archon/        # DELETE
├── frontend-task-planner-archon/       # DELETE
└── frontend-tech-refiner-archon/       # DELETE

hubara_agency/.exoclaw/                 # mover refinements/plans/results vivos a .hubara/, luego DELETE dir
frontend_dashboard/.frontend/           # idem
```

### §9.2 Pasos del PR19

1. **Migración de artifacts in-flight:** si hay refinements / plans /
   results vivos en `.exoclaw/` o `.frontend/` que aún no se cerraron en
   un PR, copiarlos a `hubara_agency/.hubara/` antes de borrar.
2. **Update referencias:**
   - `ARCHITECTURE.md §13` (Historia) — agregar entry "PR19: deprecación
     de pipelines exoclaw + frontend, hubara queda como pipeline único".
   - Cualquier doc o comentario que mencione `exoclaw-*-archon` o
     `frontend-*-archon` o los workflows legacy → reemplazar por las
     referencias hubara equivalentes.
3. **Test post-deletion:** corrida E2E del pipeline hubara para confirmar
   que ningún workflow nuevo dependía silenciosamente de un skill o
   workflow legacy.
4. **Commit message:** `chore(pipeline): PR19 — deprecate exoclaw + frontend pipelines, hubara now sole pipeline`.

### §9.3 Criterio formal de "estable" para disparar PR19

- ≥3 HUs reales mergeadas usando exclusivamente `hu-hubara-pipeline`.
- ≥1 HU multi-plugin mergeada usando exclusivamente `hu-hubara-pipeline`.
- ≥0 fallback a `hu-frontend-pipeline` o `implementar-hu` en las últimas 4 semanas.
- Review automático del hubara (V1) corrió en ≥3 PRs sin falsos positivos críticos.

Si todos los criterios verifican, ejecutar PR19. Caso contrario, esperar.

---

**Fin del blueprint de workflows.** Para PR12-PR19 detallado, ver
`HUBARA_PIPELINE_PLAN.md §6` y `§8`.
