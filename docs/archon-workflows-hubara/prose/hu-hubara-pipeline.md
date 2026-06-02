## Propósito y trigger

`hu-hubara-pipeline` es el orquestador end-to-end de **NIVEL A (plugin-level)** para implementar una HU completa de AgencyHubara: backend DEHA (Python/Temporal) + frontend FSD (React/TS) + plugin system. Es un super-pipeline **automatizado** de **77 nodos** que toma un input crudo, lo refina técnicamente, lo planifica a nivel de plugins, lo implementa (delegando el feature-level a `hu-hubara-plugin-pipeline`, NIVEL B), corre validación final consolidada de ambos stacks, pasa **3 gates pre-PR** (premortem self-review, evaluator rubric, multi-agent code-review de 5 specialists paralelos), crea **1 PR consolidado** contra `main`, archiva los artefactos + mergea spec-deltas, y dispara el review automático en background.

**Principio de diseño central:** ningún gate aplica fixes. Premortem, evaluator y code-review emiten findings y delegan al implementer vía loop; los estados ambiguos cancelan visiblemente (política anti-merge-silencioso).

### Trigger

```bash
archon workflow run hu-hubara-pipeline "<input>"
```

`<input>` (= `$ARGUMENTS` / `$USER_MESSAGE`) es **UN solo token lógico**, uno de:

- **URL de GitHub issue** — `^https://github.com/<owner>/<repo>/issues/<N>$`, debe estar **OPEN**.
- **Ruta a un `.md` local** existente con la HU.
- **Texto plano** describiendo la HU.
- **Un HU_ID existente** — `^HU-[0-9]{8}-[0-9]{4,6}-.+` para smart-resume.

**CRITICAL:** toma SOLO 1 token lógico. Pasar el formato del SUB-pipeline (`"<HU_ID> <plugin>"`) contamina el HU_ID con un espacio; `gen-hu-id` lo rechaza con `valid:'false'` (gotcha #10, run 38d8223e).

### Override env vars

- `MAX_PLUGINS_PER_HU=N` — cap de plugins en `validate-plan` (default 8).
- `FORCE_ALL_GATES=1` — `final-validation` corre todos los gates ignorando la scope-detection vs `origin/main`.
- `ARCH_EVAL_OVERRIDE=1` — mencionado SOLO como hint en `cancel-on-eval-block`; **ningún nodo lo lee**. El "override" real es desactivar el nodo manualmente.

Config global del run: `worktree.enabled=true`, `provider=claude`, `model=sonnet`, `interactive=false` (pero `rama-B-wait-fan-out-done` es un approval node que espera input igual).

### Inputs

- `$ARGUMENTS` / `$USER_MESSAGE` — input crudo.
- `$ARTIFACTS_DIR` — dir de artefactos del run (substituido literal + env var real en bash nodes).
- `$WORKFLOW_ID` — id del run.
- Env: `MAX_PLUGINS_PER_HU`, `FORCE_ALL_GATES`, `ARCH_EVAL_OVERRIDE`.
- `hubara_agency/.hubara/spinal-files.yaml` y `project-context.md` (convenciones, copiadas a artifacts por `stage-shared-files`).
- `.archon/github-project-config.yaml` (opcional — habilita GitHub Project sync fail-soft).
- Smart-resume reads: `refinements/<HU_ID>-tech.md`, `plans/<HU_ID>/plugin-manifest.yaml`.
- Per-plugin results escritos por sub-pipelines: `results/<HU_ID>/plugin-<id>-result.yaml` + `feature-results/<plugin>/task-result.yaml`.

---

## Recorrido fase por fase de una corrida

### FASE 0 — Bootstrap (prereqs + input + branch + smart-resume)

**Nodos:** `check-prereqs` → `stage-shared-files` → `resolve-input` → `gen-hu-id` → `setup-branch` → `detect-resume-state` → `project-set-refining` (+ los dump/cancel asociados).

1. **`check-prereqs`** (único nodo con `depends_on` vacío — el START): verifica `gh auth`, 8 herramientas (node/npm/bun/jq/git/curl/uv/python3), 2 convenciones commiteadas, 4 skills `hubara-*-archon` + guide, 10 commands `hubara-*`, 2 workflows, lock files, `npm ci` si falta `node_modules`, remote origin, y **protected files modificados/untracked vs `origin/main`**. Emite a stdout `OK` o el primer `FAIL_<code>`; **SIEMPRE exit 0** (el routing es por el `when` downstream, fail-closed).
   - `when == 'OK'` → `stage-shared-files`.
   - `when != 'OK'` → `dump-on-cancel-bad-prereqs` → `cancel-bad-prereqs` (END).
2. **`stage-shared-files`**: copia `spinal-files.yaml` + `project-context.md` a `$ARTIFACTS_DIR`; emite `PROJECT_ENABLED`/`PROJECT_DISABLED` (este valor **NO** se consume por ningún `when` — los `project-set-*` re-chequean la existencia del CFG). → `resolve-input`.
3. **`resolve-input`**: clasifica el input a `{issue_url, hu_id_resume, local_file, plain_text, error}` y extrae `title/body/issue_url/hu_id_override`. Issue URL → valida state OPEN; vacío → `error:empty_input`.
   - `output.type == 'error'` → `dump-on-cancel-bad-input` → `cancel-bad-input` (END).
   - else → `gen-hu-id`.
4. **`gen-hu-id`** (script bun): si `hu_id_resume` usa el override; si no construye `HU-<YYYYMMDD>-<HHMMSS>-<slug>`. Aplica el guard regex `^HU-\d{8}-\d{4,6}-[a-z0-9][a-z0-9-]*$`. Emite `valid` como **string** `'true'`/`'false'`. Es la **fuente de HU_ID/BRANCH para ~30 nodos**.
   - `output.valid == 'false'` → `dump-on-cancel-bad-hu-id` → `cancel-bad-hu-id` (END, corta en segundos ANTES de tocar git).
   - `output.valid == 'true'` → `setup-branch`.
5. **`setup-branch`**: `git fetch origin --prune`. Si la branch existe en origin → `git checkout --detach origin/$BRANCH` → emite `RESUMED`. Si no → `git push origin origin/main:refs/heads/$BRANCH` + checkout detached → `FRESH`. Persiste `hu-original.md`. La guard `when valid=='true'` evita crear `hu/null`. → bifurca a `detect-resume-state` Y `project-set-refining`.
6. **`detect-resume-state`**: chequea si `refinements/<HU_ID>-tech.md` y `plans/<HU_ID>/plugin-manifest.yaml` ya existen → JSON `{already_refined, already_planned}` (strings). Habilita el smart-resume. → `load-refinement-if-resume`, `gate-can-plan`.
7. **`project-set-refining`** (fail-soft, `trigger_rule all_done`): marca el card del Project en "Refining" si hay CFG + issue_url.

### FASE 1 — Refinar técnico

**Nodos:** `load-refinement-if-resume` → `refinar-auto` → `validate-refinement` → `commit-refinement` → `post-refinement-comment-to-issue` + `project-set-refined`.

1. **`load-refinement-if-resume`**: si existe el refinement commiteado lo copia a artifacts (`RESUMED_REFINEMENT`) para que `validate-refinement` lo vea aunque `refinar-auto` se skipee.
2. **`refinar-auto`** (skills loop, `trigger_rule all_done`, deps `load-refinement-if-resume` + `project-set-refining`): skill `hubara-tech-refiner-archon`, `loop max_iterations:2 until REFINER_OK`. Produce `hu-refinada.md` (14 secciones + §0 plugin classification). **Skipea** si `detect-resume-state.already_refined != 'true'` no se cumple (es decir, corre cuando `!= 'true'`).
3. **`validate-refinement`** (`trigger_rule all_done` — corre aunque `refinar-auto` se skipee o agote iteraciones): chequea existencia, >1000 chars, §0 + mode, 14 secciones canónicas, y protected files en §3 (`FAIL_REFINEMENT_TOUCHES_PROTECTED`). `mode=no_refinement_needed|blocked` → `PASS_NO_WORK`.
   - `!= 'PASS' && != 'PASS_NO_WORK'` → `dump-on-cancel-bad-refinement` → `cancel-bad-refinement` (END).
   - `== 'PASS' || == 'PASS_NO_WORK'` → `commit-refinement`.
4. **`commit-refinement`**: copia a `refinements/<HU_ID>-{tech,original}.md`, commit + `git push origin HEAD:$BRANCH`. **TODO el output git va a stderr** (stdout single-line `committed`/`no_changes`). → bifurca a `post-refinement-comment-to-issue`, `project-set-refined`, `load-plan-if-resume`, `gate-can-plan`.
5. **`post-refinement-comment-to-issue`** (fail-soft, `all_done`): postea UN comentario al issue con stats. **TERMINAL (→END)** — ningún nodo depende de él.
6. **`project-set-refined`** (fail-soft): marca Project "Refined"; alimenta `planificar-auto` como dep para serializar el status.

### FASE 2 — Plan plugin-level

**Nodos:** `load-plan-if-resume` → `gate-can-plan` → `planificar-auto` → `validate-plan` → `gate-plan-verdict` → `commit-plan` → `project-set-planned`.

1. **`load-plan-if-resume`**: pre-puebla `plugin-manifest.yaml` en artifacts si resume.
2. **`gate-can-plan`** (`trigger_rule all_done`, deps `detect-resume-state` + `validate-refinement` + `load-plan-if-resume`): encapsula `A&&(B||C)` que el parser no expresa con paréntesis. Lee `AP=already_planned` y `VR=validate-refinement.output` **SIN dquotes en el RHS** (shellQuote gotcha #7). 
   - `AP==true` → `SKIP_PLAN_ALREADY_PLANNED`.
   - elif `VR==PASS|PASS_NO_WORK` → `CAN_PLAN`.
   - else → `SKIP_PLAN_BAD_REFINEMENT`.
3. **`planificar-auto`** (skills loop, `all_done`): skill `hubara-plugin-planner-archon`, `loop max_iterations:2 until PLANNER_OK`. Solo si `gate-can-plan.output == 'CAN_PLAN'`. Produce `plugin-manifest.yaml` con `mode` + `plugins[]` + `plugin_batches[]` + `shared_files_intents[]`.
4. **`validate-plan`** (`all_done`, python3): valida `mode ∈ {single_plugin,multi_plugin,no_work,blocked}`, cap `≤ MAX_PLUGINS_PER_HU`, coherencia mode↔len(plugins), batches cubren exactamente los ids. Emite `PASS_<MODE>` o `FAIL_*` (su output es la verdad detallada).
5. **`gate-plan-verdict`** (default `all_success`): colapsa `validate-plan` a `PASS|FAIL` (`case PASS* → PASS`, también SIN dquotes en el RHS). Reemplaza el regex que el parser no soporta.
   - `== 'FAIL'` → `dump-on-cancel-bad-plan` (deps `validate-plan` + `gate-plan-verdict`) → `cancel-bad-plan` (END, con gate trace completo).
   - `== 'PASS'` → `commit-plan`.
6. **`commit-plan`** (deps `validate-plan` + `gate-plan-verdict`): copia a `plans/<HU_ID>/`, commit + `push HEAD:$BRANCH` (stdout single-line `committed_<N>`/`no_changes`). Es el **ancla de FASE 3**: `classify-mode`, `prewarm-uv-venv`, `project-set-planned` dependen de él.
7. **`project-set-planned`** (fail-soft) → `project-set-implementing` depende de ESTE (no de `commit-plan`) para serializar Planned→Implementing y evitar race en el GitHub API.

### FASE 3 — Implementación (rama A single / rama B multi-plugin)

**Nodos:** `project-set-implementing` → `classify-mode` → `prewarm-uv-venv` → (rama A) `rama-A-single-plugin-inline` | (rama B) `rama-B-print-fan-out-commands` → `rama-B-wait-fan-out-done` → `rama-B-merge-batch` → `rama-B-invoke-merger-if-shared` → `rama-B-commit-merger`.

1. **`prewarm-uv-venv`** (deps `commit-plan` + `project-set-implementing`, `all_done`, timeout 600000): `uv sync` + bootstrap `.env` desde `.env.example` (load-bearing — sin `.env` pytest que importa workers crashea con `MedusaSettings: base_url Field required`).
2. **`classify-mode`** (GATE de bifurcación, dep `commit-plan`): python3 re-lee el manifest commiteado → JSON `{mode, plugins[], batches[], requires_merger, single_plugin_id}`. `requires_merger` es JSON boolean → Archon lo convierte a string para los `when` (quotes obligatorias).

**RAMA A — `single_plugin`** (`when classify-mode.output.mode == 'single_plugin'`, deps `classify-mode` + `prewarm-uv-venv`):
- **`rama-A-single-plugin-inline`** (timeout 3600000): invoca `env -u CLAUDECODE archon workflow run hu-hubara-plugin-pipeline '<HU_ID> <PLUGIN_ID>'` INLINE (espera rc, log a file). `git fetch + merge --ff-only origin/$BRANCH`. Valida `plugin-<id>-result.yaml` status ∈ `{passed, passed_with_warnings}`. Emite `PASS`/`skipped`/`FAIL_SUBPIPELINE`/`FAIL_NO_PLUGIN_RESULT`/`FAIL_PLUGIN_NOT_PASSED`.

**RAMA B — `multi_plugin`** (`when ... mode == 'multi_plugin'`):
- **`rama-B-print-fan-out-commands`**: imprime 1 comando `archon workflow run hu-hubara-plugin-pipeline "<HU_ID> <p>"` por plugin del **PRIMER batch** (solo 1 batch soportado), escribe `.current-batch-plugins`. El fan-out es **MANUAL** (operador abre N terminales).
- **`rama-B-wait-fan-out-done`** (**ÚNICO nodo HUMAN-GATE de tipo approval**): espera `ready`/`abort` cuando los sub-pipelines del batch terminaron.
- **`rama-B-merge-batch`**: `merge --ff-only` (diverge → `FAIL_FF_MERGE`); por cada plugin valida result (missing → `MISSING`, `passed_with_warnings` → `WARNED` continuable, otro → `FAILED`). Si `MISSING||FAILED` → `FAIL_BATCH_INCOMPLETE`, else `BATCH_OK`.
  - `mode==multi_plugin && requires_merger=='true' && BATCH_OK` → **`rama-B-invoke-merger-if-shared`** (command `hubara-merge-intents`, condición TRIPLE) → **`rama-B-commit-merger`** (`when` SOLO `mode==multi_plugin && BATCH_OK`; el body re-chequea `requires_merger` con jq).
  - `mode==multi_plugin && != 'BATCH_OK'` → `dump-on-cancel-multi-plugin-failure` → `cancel-on-multi-plugin-failure` (END).

### FASE 4 — Validación final consolidada

**Nodos:** `check-pipeline-error` → `final-validation` (+ dump/cancel).

1. **`check-pipeline-error`** (**FAN-IN crítico de ramas A y B**, deps `rama-A-single-plugin-inline` + `rama-B-merge-batch` + `rama-B-commit-merger`, `trigger_rule all_done` — las ramas no-tomadas emiten skipped): `find $ARTIFACTS_DIR -name pipeline-error.yaml` → `OK`/`HAS_ERROR`.
   - `HAS_ERROR` → `dump-on-cancel-implement-error` → `cancel-on-implement-error` (END).
   - `OK` → `final-validation`.
2. **`final-validation`** (el gate más pesado, timeout 1200000): scope-detection vs merge-base con `origin/main` (`FORCE_ALL_GATES=1` fuerza todos). Backend (si touched): render-compose drift, `pytest -m architecture` (watchdog 120s), `lint-imports`, `pytest tests/plugins` (120s), `pytest tests/functional` (180s) — todos con I/O a file para evitar el orphan-pipe hang + pkill defensivo. Frontend (si touched): `plugins:sync`, `npm test`, `test:arch`, `tsc -b`, `build`. Playwright (si touched UI): arranca uvicorn en puerto random, `npx playwright test`. Emite `gates-summary.md` + `PASS`/`FAIL_*`.
   - `!= 'PASS'` → `dump-on-cancel-final-validation-fail` → `cancel-on-final-validation-fail` (END).
   - `== 'PASS'` → `premortem-self-review`.

### FASE 4.5 — Premortem self-review gate (GATE 1)

**Nodos:** `premortem-self-review` → `check-premortem-clean` → (loop) `loop-implementer-resolves-premortem` → `check-premortem-resolved`.

1. **`premortem-self-review`** (command `hubara-premortem`, solo si `final-validation==PASS`): imagina 30-50 failure_modes en 10 categorías → `premortem.yaml`. NO aplica fixes.
2. **`check-premortem-clean`**: cuenta `failure_modes`. → `PM_CLEAN` (0) / `PM_HAS_ISSUES` / `PM_MISSING`.
   - `PM_MISSING` → `cancel-on-premortem-missing` (**cancel DIRECTO, sin dump gemelo**, END).
   - `PM_HAS_ISSUES` → `loop-implementer-resolves-premortem`.
   - `PM_CLEAN` → (fan-in) `evaluate-pre-pr`.
3. **`loop-implementer-resolves-premortem`** (skills loop, `until PREMORTEM_LOOP_DONE`): skill `hubara-implementer-archon` en modo PROCESAR PREMORTEM. Por cada failure_mode decide según `fix_complexity` (trivial→aplica, medium→si no cambia signature, complex→defer). Actualiza `task-result.yaml`.
4. **`check-premortem-resolved`**: busca `task-result.yaml` en **AMBOS paths** (`$ARTIFACTS_DIR` + `feature-results/<plugin>/`), precedencia `broken>blocked>resolved`. → `PR_RESOLVED`/`PR_BLOCKED`/`PR_BROKEN`/`PR_RESULT_MISSING`/`PR_UNKNOWN`.
   - `PR_RESOLVED` → (fan-in) `evaluate-pre-pr`.
   - `PR_BLOCKED || PR_BROKEN || PR_RESULT_MISSING || PR_UNKNOWN` → `dump-on-cancel-premortem-blocked` → `cancel-on-premortem-blocked` (END).

### FASE 4.6 — Pre-PR evaluation gate (GATE 2)

**Nodos:** `evaluate-pre-pr` → `gate-evaluator-verdict`.

1. **`evaluate-pre-pr`** (command `hubara-evaluate`, **FAN-IN de las 2 rutas del premortem**, deps `check-premortem-clean` + `check-premortem-resolved`, `when PM_CLEAN || PR_RESOLVED`): puntúa 5 criterios contra la rúbrica → `evaluation.yaml` con `verdict` + `weighted_average` + `recommended_actions[]`.
2. **`gate-evaluator-verdict`**: colapsa a `EVAL_PASS`/`EVAL_WARN`/`EVAL_BLOCK`/`EVAL_MISSING`/`EVAL_UNKNOWN`.
   - `EVAL_PASS || EVAL_WARN` → fan-out a los 5 reviewers.
   - `EVAL_BLOCK || EVAL_MISSING || EVAL_UNKNOWN` → `dump-on-cancel-eval-block` → `cancel-on-eval-block` (END).

### FASE 4.7 — Multi-agent code review gate (GATE 3)

**Nodos:** `review-deha` ‖ `review-fsd` ‖ `review-plugin-system` ‖ `review-test-coverage` ‖ `review-security` → `synthesize-review` → `check-review-clean` → (loop) `loop-implementer-resolves-review` → `check-review-resolved`.

1. **5 reviewers `review-*`** (commands, **EN PARALELO** — todos dependen solo de `gate-evaluator-verdict`, `when EVAL_PASS || EVAL_WARN`): DEHA / FSD / plugin-system / test-coverage / security, cada uno escribe `review-findings-<area>.yaml`. Cross-ref con `premortem.yaml` para no duplicar.
2. **`synthesize-review`** (command `hubara-synthesize-review`, **FAN-IN de los 5**, `trigger_rule one_success` — **ÚNICO nodo con `one_success`** del pipeline, robusto a fallo de un specialist): consolida en `code-review-findings.yaml` + cross-ref `also_in_premortem`.
3. **`check-review-clean`**: cuenta findings. → `REVIEW_CLEAN` (0) / `REVIEW_HAS_BLOCKERS` (critical||high) / `REVIEW_HAS_MINOR` / `CR_MISSING`.
   - `CR_MISSING` → `cancel-on-review-missing` (**cancel DIRECTO, sin dump gemelo**, END).
   - `REVIEW_HAS_BLOCKERS || REVIEW_HAS_MINOR` → `loop-implementer-resolves-review` (HAS_MINOR **también** va al loop, no directo al PR).
   - `REVIEW_CLEAN` → (fan-in) `build-pr-body`.
4. **`loop-implementer-resolves-review`** (skills loop, `until CODE_REVIEW_LOOP_DONE`): aplica según `severity×fix_complexity` (critical/high trivial/medium→aplica, complex→defer, low→defer); skip de findings `also_in_premortem`.
5. **`check-review-resolved`** (mismo path-fix de 2 paths): → `REVIEW_RESOLVED`/`REVIEW_BLOCKED`/`REVIEW_BROKEN`/`RV_RESULT_MISSING`/`REVIEW_UNKNOWN`.
   - `REVIEW_RESOLVED` → (fan-in) `build-pr-body`.
   - `REVIEW_BLOCKED || REVIEW_BROKEN || RV_RESULT_MISSING || REVIEW_UNKNOWN` → `dump-on-cancel-review-blocked` → `cancel-on-review-blocked` (END).

### FASE 5 — PR + Project Done

**Nodos:** `build-pr-body` → `create-pr` → `project-set-done`.

1. **`build-pr-body`** (script bun, **FAN-IN de las 2 rutas del review**, deps `check-review-clean` + `check-review-resolved`, `when REVIEW_CLEAN || REVIEW_RESOLVED`): ensambla `pr-body.md` desde refinement/plan/results/evidencia (strip ANSI, load-bearing).
2. **`create-pr`** (`when $final-validation.output == 'PASS'` — **re-chequea final-validation, NO el review state**, defensivo): commitea visual-evidence (non-fatal), `gh pr create --base main` (reusa PR existente si hay), escribe `.pr-url`. **NO cancela** en `FAIL_PR_CREATE` (exit 0). Es el ancla de FASE 5.5/6.
3. **`project-set-done`** (fail-soft): marca "Done — PR ready".

### FASE 5.5 — Archive + spec deltas

**Nodos:** `archive-hu` → `commit-archive`.

1. **`archive-hu`** (command `hubara-archive-hu`, `when create-pr.output != 'FAIL_PR_URL_NOT_PARSEABLE'` — corre incluso si `create-pr` emitió `FAIL_PR_CREATE`): snapshot a `archive/<fecha>-<HU_ID>/` + merge de spec-deltas a `specs/`. El command NO commitea.
2. **`commit-archive`** (`trigger_rule all_done`): stagea archive + specs, commit + push. **No aborta el pipeline si falla** (side effect benigno, exit 0).

### FASE 6 — Trigger review + summary

**Nodos:** `trigger-review` → `print-final-summary`.

1. **`trigger-review`** (`all_done`): si hay `.pr-url` y existe `review-pr-hubara.yaml`, lanza `archon workflow run review-pr-hubara <PR_URL>` en background (`nohup + disown`, fire-and-forget).
2. **`print-final-summary`** (**FAN-IN final**, deps `trigger-review` + `project-set-done` + `commit-archive`, `all_done`, **nodo TERMINAL de la rama feliz → END**): imprime el banner con HU_ID/mode/branch/PR/review status.

---

## Loops y reintentos

Hay exactamente **4 nodos loop** (todos `skills` con `max_iterations:2`, es decir 1 try + 1 retry):

| Nodo | Skill | Señal de cierre (`until`) | Cuándo corre |
|---|---|---|---|
| `refinar-auto` | `hubara-tech-refiner-archon` | `REFINER_OK` | `detect-resume-state.already_refined != 'true'` |
| `planificar-auto` | `hubara-plugin-planner-archon` | `PLANNER_OK` | `gate-can-plan.output == 'CAN_PLAN'` |
| `loop-implementer-resolves-premortem` | `hubara-implementer-archon` | `PREMORTEM_LOOP_DONE` | `check-premortem-clean.output == 'PM_HAS_ISSUES'` |
| `loop-implementer-resolves-review` | `hubara-implementer-archon` | `CODE_REVIEW_LOOP_DONE` | `check-review-clean.output ∈ {REVIEW_HAS_BLOCKERS, REVIEW_HAS_MINOR}` |

**Qué cierra cada loop:** la emisión de la señal de completion (`promise`) por el agente, o el agotamiento de las 2 iteraciones. `refinar-auto` y `planificar-auto` soportan `gate_message` (feedback humano vía `$LOOP_USER_INPUT`); ambos tienen `idle_timeout 600000`. Los dos loops del implementer tienen `idle_timeout 1800000`.

**Qué pasa si el agente NO emite la señal:** el comportamiento está gobernado por el `trigger_rule` y por el nodo de verificación downstream, **no por el loop en sí**:

- **`refinar-auto`** (`trigger_rule all_done`): agotar iteraciones sin `REFINER_OK` NO bloquea — `validate-refinement` (también `all_done`) corre igual y juzga el artefacto. Si el refinement quedó incompleto/inexistente, `validate-refinement` emite `FAIL_*` → cancel visible. La señal NO es el gate; **el artefacto validado lo es**.
- **`planificar-auto`** (`all_done`): idéntico — `validate-plan` (`all_done`) juzga el `plugin-manifest.yaml` independientemente de `PLANNER_OK`. Plan ausente → `FAIL_NOT_EXISTS`.
- **`loop-implementer-resolves-premortem`**: `check-premortem-resolved` busca el `task-result.yaml` actualizado. Si el implementer no resolvió/escribió, cae en `PR_RESULT_MISSING`/`PR_UNKNOWN`/`PR_BLOCKED`/`PR_BROKEN` → cancel visible. SOLO `PR_RESOLVED` continúa.
- **`loop-implementer-resolves-review`**: simétrico — `check-review-resolved`; SOLO `REVIEW_RESOLVED` continúa, el resto cancela.

El diseño deliberado: **la señal del loop no es load-bearing; el nodo de validación/verificación que sigue es el que decide**, lo que evita que un loop que "se quedó callado" pase silenciosamente.

---

## Caminos de cancelación

El pipeline tiene **13 nodos cancel** (`type=manual`, `is_cancel=true`, todos terminales →END) y **11 nodos dump** (`dump-on-*`, bash forense que corre ANTES del cancel para capturar `diagnostic-bundle.yaml`). El patrón general: `check/gate` → `dump-on-cancel-*` → `cancel-*`, donde el `cancel` `depends_on` el `dump` (invierte el orden para capturar diagnostics antes de abortar).

**Excepción:** exactamente **2 cancels NO tienen dump gemelo** y cancelan DIRECTO desde el check node: `cancel-on-premortem-missing` y `cancel-on-review-missing`.

| Cancel node | Dump gemelo | Condición exacta (`when`) |
|---|---|---|
| `cancel-bad-prereqs` | `dump-on-cancel-bad-prereqs` | `$check-prereqs.output != 'OK'` |
| `cancel-bad-input` | `dump-on-cancel-bad-input` | `$resolve-input.output.type == 'error'` |
| `cancel-bad-hu-id` | `dump-on-cancel-bad-hu-id` | `$gen-hu-id.output.valid == 'false'` |
| `cancel-bad-refinement` | `dump-on-cancel-bad-refinement` | `$validate-refinement.output != 'PASS' && != 'PASS_NO_WORK'` |
| `cancel-bad-plan` | `dump-on-cancel-bad-plan` | `$gate-plan-verdict.output == 'FAIL'` |
| `cancel-on-multi-plugin-failure` | `dump-on-cancel-multi-plugin-failure` | `mode == 'multi_plugin' && $rama-B-merge-batch.output != 'BATCH_OK'` |
| `cancel-on-implement-error` | `dump-on-cancel-implement-error` | `$check-pipeline-error.output == 'HAS_ERROR'` |
| `cancel-on-final-validation-fail` | `dump-on-cancel-final-validation-fail` | `$final-validation.output != 'PASS'` |
| `cancel-on-premortem-missing` | **(ninguno)** | `$check-premortem-clean.output == 'PM_MISSING'` |
| `cancel-on-premortem-blocked` | `dump-on-cancel-premortem-blocked` | `$check-premortem-resolved.output ∈ {PR_BLOCKED, PR_BROKEN, PR_RESULT_MISSING, PR_UNKNOWN}` |
| `cancel-on-eval-block` | `dump-on-cancel-eval-block` | `$gate-evaluator-verdict.output ∈ {EVAL_BLOCK, EVAL_MISSING, EVAL_UNKNOWN}` |
| `cancel-on-review-missing` | **(ninguno)** | `$check-review-clean.output == 'CR_MISSING'` |
| `cancel-on-review-blocked` | `dump-on-cancel-review-blocked` | `$check-review-resolved.output ∈ {REVIEW_BLOCKED, REVIEW_BROKEN, RV_RESULT_MISSING, REVIEW_UNKNOWN}` |

### Cobertura de estados (análisis de silent-holes)

El invariante run-wide es: **solo el valor "happy" continúa; cualquier otro cancela**. La verificación por gate:

- **`check-premortem-clean`** {`PM_CLEAN`, `PM_HAS_ISSUES`, `PM_MISSING`}: `PM_CLEAN`→continúa, `PM_HAS_ISSUES`→loop, `PM_MISSING`→cancel. **Cubierto completo.**
- **`check-premortem-resolved`** {`PR_RESOLVED`, `PR_BLOCKED`, `PR_BROKEN`, `PR_RESULT_MISSING`, `PR_UNKNOWN`}: `PR_RESOLVED`→continúa; los otros 4→cancel. **Cubierto completo** (el `PR_RESULT_MISSING`/`PR_UNKNOWN` fueron incluidos explícitamente — hole-fix run b9b95fc5).
- **`gate-evaluator-verdict`** {`EVAL_PASS`, `EVAL_WARN`, `EVAL_BLOCK`, `EVAL_MISSING`, `EVAL_UNKNOWN`}: PASS|WARN→continúan; BLOCK/MISSING/UNKNOWN→cancel. **Cubierto completo.**
- **`check-review-clean`** {`REVIEW_CLEAN`, `REVIEW_HAS_BLOCKERS`, `REVIEW_HAS_MINOR`, `CR_MISSING`}: CLEAN→PR, HAS_BLOCKERS||HAS_MINOR→loop, CR_MISSING→cancel. **Cubierto completo.**
- **`check-review-resolved`** {`REVIEW_RESOLVED`, `REVIEW_BLOCKED`, `REVIEW_BROKEN`, `RV_RESULT_MISSING`, `REVIEW_UNKNOWN`}: RESOLVED→continúa; los otros 4→cancel. **Cubierto completo.**

**Riesgo residual de silent-hole (señalado en el modelo, no eliminado):** `build-pr-body` `depends_on` AMBOS `check-review-clean` y `check-review-resolved` con un `when` OR (`REVIEW_CLEAN || REVIEW_RESOLVED`). Si ninguno emitiera el valor happy, `build-pr-body` se skipearía y `create-pr` (`all_success` sobre `build-pr-body`) también se skipearía silenciosamente. En la práctica los cancel nodes (`CR_MISSING` y los 4 estados de `check-review-resolved`) cubren esos estados PRIMERO, así que el hole no se materializa — pero la cobertura depende de que esos cancels disparen, no de un default explícito en `build-pr-body`. La misma estructura aplica a `evaluate-pre-pr` (fan-in OR de las 2 rutas del premortem), protegida igual por los cancels upstream.

Además, **`create-pr` no cancela en `FAIL_PR_CREATE`** (exit 0): el run no aborta; `archive-hu`/`print-final-summary` corren con `all_done`. Y `cancel-on-eval-block` menciona `ARCH_EVAL_OVERRIDE=1` como hint pero **ningún nodo lee esa env var** — el override real es desactivar el nodo manualmente.

---

## Invariantes y env vars

- **`HU_ID`** — generado/validado por `gen-hu-id` con regex `^HU-\d{8}-\d{4,6}-[a-z0-9][a-z0-9-]*$`. Inmutable, fuente para ~30 nodos. `valid` es **string** `'true'`/`'false'`, no boolean.
- **`BRANCH`** = `hu/<HU_ID>`.
- **`ARTIFACTS_DIR`** — dir de artefactos (literal + env var real en bash nodes).
- **`WORKFLOW_ID`** — id del run (substitución literal).
- **`mode`** ∈ `{single_plugin, multi_plugin, no_work, blocked}` — emitido por el planner en `plugin-manifest.yaml`, re-leído y expuesto por `classify-mode` como JSON. Bifurca rama A (inline) vs rama B (fan-out manual). `requires_merger` es JSON boolean convertido a string por Archon (quotes obligatorias en los `when`).
- **`MAX_PLUGINS_PER_HU`** (default 8) — leído por `validate-plan`.
- **`FORCE_ALL_GATES`** (default 0) — leído por `final-validation`.
- **`ARCH_EVAL_OVERRIDE`** — solo hint en un mensaje de cancel; no leído por ningún nodo.

**Estrategia de branch (load-bearing):**
- `setup-branch` usa **`git checkout --detach origin/$BRANCH`** (HEAD detached) para sobrevivir worktrees stale + concurrencia multi-worktree (gotcha #9 + run 710e0eb6) — evita el error `branch already checked out elsewhere`.
- Branch fresh: `git push origin origin/main:refs/heads/$BRANCH`.
- **TODOS los pushes posteriores**: `git push origin HEAD:$BRANCH` (porque el HEAD está detached).
- Concurrencia entre pushes: `pull --rebase + retry`.

**Smart-resume:** `detect-resume-state` chequea refinement/plan commiteados → skipea FASE 1/2 vía `gate-can-plan` + los `when` de `refinar-auto`/`planificar-auto`. Los nodos `load-*-if-resume` pre-pueblan los artefactos para que los validadores los vean aunque el skill se skipee.

**Invariantes adicionales:**
1. Cada cancel de fallo tiene su dump gemelo que corre ANTES (excepto `cancel-on-premortem-missing` y `cancel-on-review-missing`).
2. Los bash gate nodes emiten **stdout single-line canónico** (diagnostics a stderr) porque downstream usa strict `==` (gotchas #7/#8).
3. Assignments de output refs en gate bash nodes van **SIN dquotes en el RHS** (`AP=$node.output`, gotcha #7).
4. **Ningún gate aplica fixes** — premortem/evaluator/review delegan al implementer; deferred complex → cancel visible.
5. `project-set-*` son **fail-soft** (`trigger_rule all_done`, escriben `skipped` si no hay CFG; `awk -F:` + `gsub` para keys con em-dash).
6. Los estados-agujero (`PM_MISSING`, `CR_MISSING`, `PR_RESULT_MISSING`/`PR_UNKNOWN`, `RV_RESULT_MISSING`/`REVIEW_UNKNOWN`, `EVAL_MISSING`/`EVAL_UNKNOWN`) **cancelan fuerte**; solo el valor happy continúa.

---

## Gotchas y modos de fallo conocidos

- **HU_ID contaminado por args del sub-pipeline (gotcha #10, run 38d8223e):** pasar `"<HU_ID> <plugin>"` (formato del sub-pipeline) al pipeline principal mete un espacio en el HU_ID → branch con espacio → refspec git inválido → 30 min de run muerto. El guard regex de `gen-hu-id` lo corta en segundos con `valid:'false'` + hint que distingue "tiene espacios" vs "no matchea formato". `setup-branch` tiene `when valid=='true'` para no crear `hu/null`.

- **shellQuote single-quote wrapping (gotcha #7, run cadd3d61):** Archon envuelve `$node.output` en single quotes. En `gate-can-plan` y `gate-plan-verdict` los assignments del RHS van **SIN dquotes** (`AP=$node.output`) — con dquotes quedaría el literal `'false'` con comillas y el `==` fallaría silenciosamente. **No "arreglar" a `AP="$node.output"`.** Bug introducido en commit 135f646; si el planner se skipea por esto, `validate-plan` emite `FAIL_NOT_EXISTS` (mensaje engañoso).

- **stdout multi-línea rompe el `==` (gotcha #8, run cadd3d61):** `git commit`/`push` escupen multi-línea; en `commit-refinement`/`commit-plan`/todos los gates y checks, TODO el output diagnostic va a stderr y stdout queda single-line canónico. Si no, downstream hace silent skip.

- **task-result.yaml en path equivocado (gotcha #11, run 894495e1):** el implementer multi-plugin actualiza `task-result.yaml` en `feature-results/<plugin>/`, NO en `$ARTIFACTS_DIR`. `check-premortem-resolved` y `check-review-resolved` buscan en **AMBOS paths** (precedencia broken>blocked>resolved); si no, `PR_RESULT_MISSING` falso → cancel engañoso "blocked" con el trabajo stranded.

- **Detached HEAD multi-worktree (gotcha #9 + run 710e0eb6):** N worktrees compartiendo `hu/<HU_ID>` hacían que git rechazara el checkout. `setup-branch` usa `--detach` y todos los pushes usan `HEAD:$BRANCH`.

- **Orphan-pipe hang en final-validation (runs 0538c537/710e0eb6/ee8436ff):** orphans de pytest/uv/temporal-test-server heredan el stdout pipe → Archon espera EOF hasta el global timeout aunque bash ya salió. Fix: watchdog file-redirect (I/O a file) + pkill defensivo. El full `pytest -q` fue removido (run ee8436ff).

- **Bootstrap `.env` antes del fan-out (run 940be3b9):** sin `.env`, `pytest -m architecture` que importa workers crashea con `MedusaSettings: base_url Field required`. `prewarm-uv-venv` copia `.env.example`→`.env`.

- **Race en GitHub Project API (2026-05-26):** `project-set-implementing` `depends_on` `project-set-planned` (no `commit-plan`) para serializar Planned→Implementing; los `project-set-*` usan `awk -F:` + `gsub` porque keys como `Done — PR ready` con em-dash rompen el FS default de awk.

- **ANSI garbage en el PR body (run 5d99ee6b):** el operador vio basura ANSI `^[[2m` en el PR; `build-pr-body` aplica `stripAnsi`.

- **nested archon-in-claude-code hang:** `rama-A-single-plugin-inline` y `trigger-review` usan `env -u CLAUDECODE` para evitar el hang al invocar archon dentro de claude-code.

- **DIRTY protected files load-bearing:** Archon copia `.archon/` al worktree; si `main` local tiene cambios sin commit en protected paths, el meta-gate final falla. `check-prereqs` lo detecta vs `origin/main` (`FAIL_DIRTY_PROTECTED_FILES`).

- **Solo 1 batch multi-plugin soportado (comentario L1347-1351):** no existen nodos `rama-B-batch-2/3`. HUs con >1 batch topológico requieren re-correr el orquestador por batch. El fan-out de la rama B es **manual** (operador abre N terminales y luego responde `ready` en el approval node).

- **Asimetría deliberada de estados missing:** premortem usa `PR_RESULT_MISSING`, review usa `RV_RESULT_MISSING` (prefijos distintos a propósito — fácil de confundir).

- **`create-pr.when` re-chequea `final-validation`, no el review state** (redundancia defensiva); reusa PR existente si ya hay uno.
