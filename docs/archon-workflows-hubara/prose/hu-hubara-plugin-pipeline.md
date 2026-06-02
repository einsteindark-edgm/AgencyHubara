## Propósito y trigger

Este es el **sub-pipeline por plugin** (`hu-hubara-plugin-pipeline`). Resuelve un problema acotado: dado UN plugin de una HU ya refinada y ya planificada a nivel de plugins por el orquestador, produce el **DAG feature-level** dentro de ese plugin, lo implementa task por task con gates deterministas, y reporta de vuelta al orquestador un veredicto autoritativo del plugin (`plugin-<id>-result.yaml`). Corre dentro de un worktree dedicado (`worktree.enabled: true`).

Lo que NO hace: NO decide single vs multi_plugin. Esa clasificación vive aguas arriba (en `hu-hubara-tech-refiner` / el orquestador `hu-hubara-pipeline`). Cada invocación maneja **exactamente un** `plugin_id`.

**Invocación exacta:**

```
archon workflow run hu-hubara-plugin-pipeline "<HU_ID> <plugin_id>"
```

El contrato de input es estricto: **exactamente 2 tokens separados por whitespace**. `$ARGUMENTS` se parte con `awk '{print $1}'` (HU_ID) y `awk '{print $2}'` (PLUGIN_ID). Ejemplo: `"HU-20260517-143025-add-image-tool chats"`.

Se invoca de tres formas:
- **(a)** inline por `hu-hubara-pipeline` cuando `mode=single_plugin` (una vez),
- **(b)** fan-out manual N veces por el operador cuando `mode=multi_plugin` (una terminal por plugin),
- **(c)** standalone para debug de un plugin.

**Inputs requeridos:**
- `$ARGUMENTS` = `"<HU_ID> <plugin_id>"` (2 tokens). Un input de 1 token — la forma del pipeline principal — dejaría `plugin_id` vacío y dispararía `cancel-bad-input`.
- `HU_ID` debe matchear `^HU-[0-9]{8}-[0-9]{4,6}-` (validado en `parse-input`; **más laxo** que el regex del pipeline principal — solo exige guion final, sin clase de chars de slug).
- `plugin_id` debe matchear `^[a-z][a-z0-9_]*$`.
- Branch `hu/<HU_ID>` ya existente en origin (creada por `hu-hubara-pipeline`).
- Pre-commiteados: `plans/<HU_ID>/plugin-manifest.yaml` (FASE 2 del orquestador), `refinements/<HU_ID>-tech.md` (FASE 1), `spinal-files.yaml`, `project-context.md`.
- Skills presentes: `hubara-feature-planner-archon`, `hubara-implementer-archon`, `hubara-architecture-guide`.
- Tooling en PATH: `gh` (autenticado), `uv`, `npm`, `jq`, `python3`.
- Env opcional: `MAX_FEATURES_PER_PLUGIN` (default 12).

El nivel de archivo declara `interactive: false`, pero el loop del feature-planner lleva un `gate_message` (es interactivo dentro de su loop).

## Recorrido fase por fase (una corrida)

### FASE 0 — Bootstrap & input validation

Nodos: `parse-input`, `cancel-bad-input`, `check-prereqs`, `cancel-bad-prereqs`, `checkout-branch`, `cancel-bad-checkout`, `stage-plugin-context`, `detect-resume-feature-plan`.

1. **`parse-input`** (root, START se engancha acá). Parte `$ARGUMENTS` con awk en HU_ID/PLUGIN_ID. Emite un objeto jq `{type:error,...}` (saliendo 0) si cualquier token está vacío, si HU_ID falla `grep -qE '^HU-[0-9]{8}-[0-9]{4,6}-'`, o si PLUGIN_ID falla `^[a-z][a-z0-9_]*$`. En éxito emite `{type:ok, hu_id, plugin_id, branch:('hu/'+h), plan_dir, plugin_manifest, feature_plan_dir, results_dir, plugin_result_file, feature_results_dir}`. **SIEMPRE sale 0** — el ruteo es por `output.type`, no por exit code. Este JSON completo es la **fuente de verdad de toda la corrida**: casi todo nodo bash downstream re-deriva HU_ID/PLUGIN_ID/BRANCH con `echo $parse-input.output | jq -r '.field'`.
   - Camino cancel: `when $parse-input.output.type == 'error'` → **`cancel-bad-input`** (manual). Echo del output + el usage string. Como `parse-input` siempre sale 0, el `trigger_rule: all_success` por defecto se cumple y el ruteo lo decide el `when`.
   - Camino OK: `when $parse-input.output.type == 'ok'` → `check-prereqs`. Ramas mutuamente excluyentes.

2. **`check-prereqs`**. Secuencia de chequeos guardados, cada uno `|| { echo FAIL_*; exit 0; }`: `gh auth status` (FAIL_GH_AUTH), `command -v uv/npm/jq/python3` (FAIL_NO_UV/NPM/JQ/PYTHON3), `spinal-files.yaml` y `project-context.md` (FAIL_MISSING_SPINAL_FILES / FAIL_MISSING_PROJECT_CONTEXT), los SKILL.md de los 3 skills (FAIL_MISSING_FEATURE_PLANNER / FAIL_MISSING_IMPLEMENTER / FAIL_MISSING_GUIDE), `git ls-remote origin HEAD` (FAIL_NO_ORIGIN). Echo `OK` solo si todos pasan. `timeout 60000`. A pesar de `set -e`, siempre sale 0 con un status de una sola línea. (No chequea scope de gh project — no hay token FAIL_GH_NO_PROJECT_SCOPE acá, a diferencia del pipeline principal.)
   - Camino cancel: `when $check-prereqs.output != 'OK'` → **`cancel-bad-prereqs`**.
   - Camino OK: `when $check-prereqs.output == 'OK'` → `checkout-branch`.

3. **`checkout-branch`**. `set -e`. Lee BRANCH/HU_ID vía jq. `git fetch origin --prune`. Si `git ls-remote --exit-code --heads origin $BRANCH` falla → `FAIL_BRANCH_NOT_FOUND` y **exit 1**. Si no, `git checkout --detach origin/$BRANCH` (HEAD detached). Luego verifica que `plans/$HU_ID/plugin-manifest.yaml` exista (si no, `FAIL_NO_PLUGIN_MANIFEST`, exit 1) y `refinements/$HU_ID-tech.md` exista (si no, `FAIL_NO_REFINEMENT`, exit 1). Echo `BRANCH_READY` en éxito total.
   - **Diferencia crítica con FASE 0 previa**: los caminos FAIL acá salen **1 (no-cero)**.
   - Camino cancel: `when $checkout-branch.output != 'BRANCH_READY'` → **`cancel-bad-checkout`** con `trigger_rule: all_done` (multi-línea: decodifica las 3 causas + guía de `git worktree list` / `git worktree remove --force`). El `all_done` es **obligatorio** acá porque los FAIL salen 1; con el `all_success` por defecto este cancel se SKIPEARÍA en exit no-cero y el error real quedaría enmascarado por el `FAIL_NOT_EXISTS` engañoso de `cancel-bad-feature-plan` (cascading-skip; lección del run 52fa70e0).
   - Camino OK: `BRANCH_READY` → `stage-plugin-context` (edge `sequence`, sin condición).

4. **`stage-plugin-context`**. `set -e`. Copia a `$ARTIFACTS_DIR`: `spinal-files.yaml`, `project-context.md`, `plugin-manifest.yaml`, `refinements/$HU_ID-tech.md` (→ `hu-refinada.md`). Luego un heredoc python3 carga `plugin-manifest.yaml`, busca la entry `plugins[]` con `id==PLUGIN_ID`; si no la encuentra **levanta `SystemExit('FAIL_PLUGIN_NOT_IN_MANIFEST...')`** listando los ids declarados. Si la encuentra, arma el dict `work` (hu_id, plugin_id, work_summary, layers, template default 'A', affects_layers_detail, affects_shared_files, estimated_tasks default 1, risk default 'low', depends_on_plugins) y lo dumpea a `$ARTIFACTS_DIR/plugin-work.yaml`, luego `OK`.
   - Sin `when` + `all_success` por defecto: corre solo si `checkout-branch` tuvo éxito (BRANCH_READY), así la cascada de fallo de checkout lo skipea naturalmente.
   - **RIESGO SILENT-HOLE (confirmado)**: `FAIL_PLUGIN_NOT_IN_MANIFEST` levanta `SystemExit` (no-cero) bajo `set -e`, pero **NO hay nodo cancel dedicado** que guarde `stage-plugin-context`. Un crash acá solo se manifiesta como skips en cascada / re-evaluación `all_done` downstream, NO como un mensaje de cancel claro.

5. **`detect-resume-feature-plan`** (smart-resume probe). Lee HU_ID/PLUGIN_ID. Chequea `plans/$HU_ID/feature-plans/$PLUGIN_ID/feature-plan-manifest.yaml`. Si existe: lo copia a `$ARTIFACTS_DIR`, y si existe `tareas/` copia `F*.md` (errores ignorados con `|| true`), echo `RESUMED_FEATURE_PLAN`. Si no, echo `NO_RESUME`. Es gate que rutea al planner; también es dep de `validate-feature-plan`.

### FASE 1 — Feature-level plan (dentro del plugin)

Nodos: `planificar-feature-auto`, `validate-feature-plan`, `cancel-bad-feature-plan`, `commit-feature-plan`, `prewarm-uv-venv`.

6. **`planificar-feature-auto`** (skills + loop). `when $detect-resume-feature-plan.output != 'RESUMED_FEATURE_PLAN'` → o sea **se SKIPEA entero en smart-resume**. Loop `{max_iterations:2, until:FEATURE_PLANNER_OK, skills:[hubara-feature-planner-archon], gate_message, prompt}`. El prompt: descompone el plugin en un DAG feature-level leyendo `$ARTIFACTS_DIR/{plugin-work.yaml, plugin-manifest.yaml, hu-refinada.md, spinal-files.yaml, project-context.md}` y escribiendo `feature-plan-manifest.yaml` + `tareas/F<NN>-<slug>.md` (uno por task), cargando solo las secciones del guide que matchean el template del plugin (A/B/C/D de `plugin-work.yaml`). Feedback vía `$LOOP_USER_INPUT`. Señal de completion `<promise>FEATURE_PLANNER_OK</promise>`. `gate_message` pide al operador revisar manifest + task files y decir 'ok'/'aprobado' o pedir ajustes. `idle_timeout 600000`. Escribe SOLO en `$ARTIFACTS_DIR` (efímero) — el commit durable es después.

7. **`validate-feature-plan`** (gate, **FAN-IN**). `depends_on: [planificar-feature-auto, detect-resume-feature-plan]` con `trigger_rule: all_done` → corre **tanto si el planner corrió (camino auto) como si fue skipeado (camino smart-resume, donde detect-resume ya stageó el manifest)**. Guardado por `when $checkout-branch.output == 'BRANCH_READY'`. Chequea que `$ARTIFACTS_DIR/feature-plan-manifest.yaml` exista (si no, `FAIL_NOT_EXISTS`). Heredoc python3: carga yaml; si no es dict → `FAIL_NOT_DICT`; si `mode=='blocked'` → `PASS_BLOCKED`; si tasks no es lista no-vacía → `FAIL_NO_TASKS`; si `len(tasks) > MAX` (default 12) → `FAIL_TOO_MANY_TASKS`; por task: sin id → `FAIL_TASK_NO_ID`, id no `F`+dígitos → `FAIL_BAD_TASK_ID`, sin archivo en `tareas/` empezando con `<id>-` → `FAIL_MISSING_TASK_FILE_<id>`; si todo bien → `PASS`. Todos los caminos salen 0.
   - El `when checkout==BRANCH_READY` es el **fix documentado del run 52fa70e0**: sin él, en un fallo de checkout este nodo correría vía `all_done` y emitiría el engañoso `FAIL_NOT_EXISTS` en vez de dejar que `cancel-bad-checkout` aflore el error real.
   - **AMBIGÜEDAD**: emite tanto `PASS` como `PASS_BLOCKED`, pero todo consumidor downstream keya en `=='PASS'` (commit/implement) o `!='PASS'` (cancel) — así un plan legítimamente bloqueado (`mode:blocked` → `PASS_BLOCKED`) rutea a `cancel-bad-feature-plan` (tratado como fallo de validación), **sin handler distinto de blocked**.

8. Bifurcación de `validate-feature-plan`:
   - Camino cancel: `when $checkout-branch.output == 'BRANCH_READY' && $validate-feature-plan.output != 'PASS'` → **`cancel-bad-feature-plan`** (multi-línea: decodifica FAIL_TOO_MANY_TASKS, FAIL_MISSING_TASK_FILE_F<NN>, FAIL_BAD_TASK_ID + recovery genérico). El guard compuesto `checkout==BRANCH_READY` previene un fire confuso cuando checkout falló → validate skipeado → output '' (que es `!= 'PASS'`). **CAVEAT**: por ser `!= 'PASS'`, un `PASS_BLOCKED` legítimo también dispara este cancel. `all_success` por defecto vale porque `validate-feature-plan` siempre sale 0.
   - Camino OK: `when $validate-feature-plan.output == 'PASS'` → `commit-feature-plan`.

9. **`commit-feature-plan`**. `set -e`. `DIR=plans/$HU_ID/feature-plans/$PLUGIN_ID`; copia `feature-plan-manifest.yaml` + `tareas/F*.md` ahí. `git add DIR` (>&2). Si el diff staged es no-vacío: `git commit` (>&2) + `git push origin HEAD:$BRANCH` (tail -3 >&2). Si `PIPESTATUS[0] != 0` → `git pull --rebase origin $BRANCH` + retry push; si falla dos veces, warn 'commit local, no en origin'. Echo `committed_<N>`. Si no, `no_changes`. **Disciplina stdout**: todo output git va a >&2; stdout es una sola línea (gotcha #8). El push usa `HEAD:$BRANCH` por el detached HEAD. `when ==PASS` excluye `PASS_BLOCKED` (un plan bloqueado nunca commitea — ya fue cancelado por `cancel-bad-feature-plan`).

10. **`prewarm-uv-venv`**. `depends_on: [commit-feature-plan]`, `trigger_rule: all_done`, sin `when` → corre aun si `commit-feature-plan` emitió `no_changes` o tuvo warnings. Si existe `hubara_agency`, cd ahí y `uv sync` (tail -10). **Siempre echo `ok`** — best-effort, nunca falla la corrida. `timeout 600000` (10 min) para tolerar cold sync. Es una de las dos deps de `implementar-secuencial`.

### FASE 2 — Sequential implement (loop por-task gateado)

Nodo: `implementar-secuencial`.

11. **`implementar-secuencial`** (skills + loop, **FAN-IN**). `depends_on: [commit-feature-plan, prewarm-uv-venv]`, `trigger_rule: all_done` (corre aunque prewarm sea best-effort), `when $validate-feature-plan.output == 'PASS'` (excluye `PASS_BLOCKED`). Loop `{max_iterations:50, until:NEVER_AI_SIGNAL, fresh_context:true, skills:[hubara-implementer-archon], until_bash, prompt}`. `idle_timeout 1800000`.
   - **El AI prompt restringe al implementer a SOLO escribir código de la próxima task pendiente** (itera `parallel_batches[]` luego `tasks[]` por F-id ascendente; primera task cuyo `F<id>-result.yaml` falte o no esté `status:passed` en RESULTS_DIR), leyendo task.md + secciones selectivas del guide, corriendo verificación §10, escribiendo `$ARTIFACTS_DIR/task-result.yaml` con status + wiring_intents — **NO git ops**; si existe `$ARTIFACTS_DIR/test-failures.md` (feedback de gate) debe arreglar la MISMA task.
   - **El `until_bash` es el controlador real** (la señal AI `NEVER_AI_SIGNAL` es un centinela que el AI nunca emite → la terminación la deciden los exit codes del `until_bash`, capeada a 50 iteraciones): limpia result.yaml stale sin commit matcheante; si el status es `passed|passed_with_warnings` **RE-CORRE los gates deterministas** según FILES_TOUCHED (backend: `pytest -q`, `pytest -m architecture`, `lint-imports`; render-compose drift si tocó un manifest `*plugin.yaml`; frontend: `npm test --run`, `npm run test:arch`, `npx tsc -b`, `npm run build`; Playwright E2E con un FastAPI en background en un puerto libre random si tocó UI), logueando a `test-failures.md`. En fallo de gate → **det-retry hasta MAX_DET_RETRIES=2** (rm task-result.yaml + task.md, exit 1 para re-loop); en agotamiento → reescribe `status→blocked`, revierte `src/`, `SHOULD_COMMIT_BROKEN_CODE=0`. En éxito de gate → copia result.yaml a RESULTS_DIR, `git add src/k8s/compose/.hubara/plugins + result`, resetea node_modules/dist/.vite/coverage/lock/.venv, commitea y pushea `HEAD:$BRANCH` (pull --rebase + retry). Para status no-passed clasifica transient (`blocked_reason in {command_timeout,regression}`) → **1 auto-retry**, si no escribe `pipeline-error.yaml` y **exit 0**. Finalmente `TOTAL=grep -cE '^  - id: F[0-9]+'` del manifest vs `PASSED=find F*-result.yaml con '^status: passed'`; **exit 0 cuando PASSED>=TOTAL>0, si no exit 1 para continuar**.
   - `fresh_context:true` borra el contexto AI cada iteración → todo el estado cross-iteración vive SOLO en archivos (`task-result.yaml`, `det-retries-<TASK>.count`, `retries-<TASK>.count`, `test-failures.md`, RESULTS_DIR/*-result.yaml, `pipeline-error.yaml`).
   - **MATIZ SILENT-HOLE CRÍTICO**: un fallo de task PERMANENTE **sale 0 (NO crashea)**, escribiendo `pipeline-error.yaml` — así el loop "completa exitosamente" y el veredicto fallido se recomputa downstream por `write-plugin-result`, NO por un cancel in-pipeline.

### FASE 3 — Report back to orchestrator

Nodos: `check-pipeline-error`, `write-plugin-result`, `print-summary`.

12. **`check-pipeline-error`** (gate). `depends_on: [implementar-secuencial]`, `trigger_rule: all_done`, sin `when` → corre sin importar cómo terminó el loop. Si existe `$ARTIFACTS_DIR/pipeline-error.yaml`, lo catea a >&2 y echo `HAS_ERROR`; si no, `OK`.
   - **SILENT-HOLE / DESIGN FLAG (confirmado)**: emite `HAS_ERROR` pero **NADA downstream gatea en él**. `write-plugin-result` depende de él con `all_done` y SIN `when` → `HAS_ERROR` **NO cancela la corrida**. La señal de fallo permanente es efectivamente informativa; el veredicto 'failed' real lo recomputa `write-plugin-result` independientemente vía scanning de missing-tasks / result.yaml no-passed.

13. **`write-plugin-result`**. `set -e`. `depends_on: [check-pipeline-error]`, `trigger_rule: all_done`, **sin `when` → SIEMPRE corre**. Es el paso de report-back que lee el orquestador y la **fuente canónica del veredicto del plugin** (independiente del HAS_ERROR de check-pipeline-error).
   - **COMPLETENESS**: parsea `feature-plan-manifest` (`grep -oE '^  - id: F[0-9]+'`) para los ids PLANNED; por cada uno, si `FEATURE_RDIR/<tid>-result.yaml` falta → lo agrega a MISSING_TASKS.
   - **STATUS AGGREGATION**: escanea `FEATURE_RDIR/F*-result.yaml`; `passed` → PASSED++; `passed_with_warnings` → PASSED++ & WARNED++ & WARNED_TASKS; cualquier otra cosa → ALL_PASSED=false & FAILED_TASKS.
   - **Status del plugin**: si MISSING_TASKS no-vacío → `failed` (**NUNCA passed**); elif ALL_PASSED y TOTAL>0 → `passed_with_warnings` si WARNED>0 si no `passed`; else `failed`.
   - Escribe `results/<HU_ID>/plugin-<PLUGIN_ID>-result.yaml` (version, hu_id, plugin_id, pipeline, date, status, feature_tasks_planned/total/passed/with_warnings, missing_tasks/failed_tasks/warned_tasks). `git add results/`, y si el diff staged es no-vacío commit + push `HEAD:$BRANCH` (pull --rebase + retry). Echo `plugin_result_committed status=<...> tasks=<PASSED>/<TOTAL> warnings=<WARNED>`.
   - Dos fixes encodeados: **(1) completeness check** (run b9b95fc5) — cuenta PLANNED vs result files, así un loop que completó solo F01 de F01/F02/F03 reporta `failed` con `missing_tasks` explícito, no `passed`; **(2) `passed_with_warnings` cuenta como passing** (run 25512e9) para que el `rama-B-merge-batch` del orquestador no se cancele mal.

14. **`print-summary`** (terminal, END se engancha acá). `depends_on: [write-plugin-result]`, `all_success` por defecto. Lee HU_ID/PLUGIN_ID, lee RESULT_STATUS de `grep '^status:'` del result.yaml (default 'unknown' si falta). Catea una caja ASCII con HU id, plugin, status, branch `hu/<HU_ID>`, y guía condicional: si `passed`, el orquestador colecta el result, avanza a FASE 4 e invoca el merger si se tocaron shared files (el operador vuelve a su terminal y dice 'ready'); si `failed`, apunta al feature-results dir y lista opciones (re-lanzar el sub-pipeline para smart-resume, hand-editar el plan, o abandonar el plugin). `timeout 5000`. Solo informativo; no pushea ni muta.

## Loops y reintentos

Hay **dos** loops, con semánticas opuestas de terminación:

1. **`planificar-feature-auto`** — `max_iterations:2`, `until:FEATURE_PLANNER_OK`, **interactivo** (`gate_message` + `$LOOP_USER_INPUT`). Lo cierra la señal AI `<promise>FEATURE_PLANNER_OK</promise>` que el feature-planner emite cuando el operador aprueba ('ok'/'aprobado'). `idle_timeout 600000`. Si el agente NO emite la señal: el loop se agota a las 2 iteraciones. El modelo no especifica un manejo dedicado post-agotamiento aquí; lo que sigue es `validate-feature-plan` (fan-in `all_done`), que validará lo que haya en `$ARTIFACTS_DIR` — si el manifest no existe o está mal, emite `FAIL_*` y rutea a `cancel-bad-feature-plan`.

2. **`implementar-secuencial`** — `max_iterations:50`, `until:NEVER_AI_SIGNAL`, `fresh_context:true`. **No hay señal AI real**: `NEVER_AI_SIGNAL` es un centinela que el AI **nunca** emite. La terminación la conduce **enteramente el `until_bash` por exit code**: `exit 0 = stop`, `exit 1 = continue`, capeado a 50 iteraciones. Sale 0 (stop) en dos casos:
   - **todas las tasks pasaron** (`PASSED >= TOTAL > 0`), o
   - **un fallo permanente** ocurrió (escribió `pipeline-error.yaml` y sale 0 — NO crashea).
   
   Reintentos internos del `until_bash` (estado en disco por `fresh_context`):
   - **det-retries** (`MAX_DET_RETRIES=2`): en fallo de gate determinista, borra task-result.yaml+task.md y sale 1 para re-loopear; al agotarse, reescribe `status→blocked` y revierte `src/`.
   - **transient retries** (`max 1`): para status no-passed con `blocked_reason in {command_timeout, regression}`, 1 auto-retry; si no, `pipeline-error.yaml` + exit 0.
   
   Como el AI nunca emite la señal, el riesgo de "el agente no completa" no aplica de la forma usual: el `until_bash` siempre decide. El borde real es agotar las **50 iteraciones** sin que `PASSED >= TOTAL` ni se escriba `pipeline-error.yaml` — en ese caso el loop para por cap y el veredicto lo recomputa `write-plugin-result` (que reportará `failed` por `missing_tasks`).

## Caminos de cancelación

Hay **4 nodos cancel** (todos `manual`, usan la key `cancel:`):

| Nodo cancel | Condición `when` exacta | `trigger_rule` |
|---|---|---|
| `cancel-bad-input` | `$parse-input.output.type == 'error'` | `all_success` (default) |
| `cancel-bad-prereqs` | `$check-prereqs.output != 'OK'` | `all_success` (default) |
| `cancel-bad-checkout` | `$checkout-branch.output != 'BRANCH_READY'` | **`all_done`** |
| `cancel-bad-feature-plan` | `$checkout-branch.output == 'BRANCH_READY' && $validate-feature-plan.output != 'PASS'` | `all_success` (default) |

Notas de fiabilidad:
- `cancel-bad-input`, `cancel-bad-prereqs`, `cancel-bad-feature-plan` usan `all_success` por defecto y son fiables **porque sus nodos guardia (`parse-input`, `check-prereqs`, `validate-feature-plan`) siempre salen 0** → `all_success` se cumple y el `when` decide.
- `cancel-bad-checkout` usa `all_done` **a propósito**: los caminos FAIL de `checkout-branch` salen 1, y con `all_success` este cancel se skipearía en exit no-cero, enmascarando el error real.

**Cobertura de estados (riesgo de silent-hole):** el conjunto de gates **NO cubre todos los estados posibles**. Hay tres agujeros confirmados:

1. **`stage-plugin-context` no tiene cancel dedicado.** Puede crashear con `FAIL_PLUGIN_NOT_IN_MANIFEST` (`set -e` + `SystemExit`, exit no-cero). No hay nodo cancel que lo guarde — el crash solo se manifiesta como skips en cascada / re-evaluación `all_done` downstream, no como un mensaje claro.

2. **`PASS_BLOCKED` no tiene handler distinto.** `validate-feature-plan` puede emitir `PASS_BLOCKED` (cuando el plan tiene `mode:blocked`), pero todos los consumidores keyan en `=='PASS'` / `!='PASS'`. Un plan legítimamente bloqueado rutea a `cancel-bad-feature-plan` — tratado como fallo de validación, sin path de blocked separado.

3. **`check-pipeline-error` emite `HAS_ERROR` pero ningún nodo gatea en él.** `write-plugin-result` depende de él con `all_done` y SIN `when`, así que `HAS_ERROR` **no cancela la corrida**. Además, un fallo de task permanente en `implementar-secuencial` **sale 0** (escribe `pipeline-error.yaml`, no crashea). Consecuencia de diseño: **el fallo nunca aflora por un cancel dentro de este sub-pipeline** — solo aflora vía el `status` de `plugin-<id>-result.yaml` que recomputa `write-plugin-result` (que reporta `failed` por `missing_tasks` o por result.yaml no-passed). Un `pipeline-error.yaml` solo, con todos los result files presentes-y-passed, NO forzaría por sí mismo el status `failed`.

## Invariantes y env vars

- **`$parse-input.output`** — fuente de verdad de toda la corrida: objeto JSON con `hu_id`, `plugin_id`, `branch=hu/<HU_ID>`, y todos los paths derivados bajo `hubara_agency/.hubara/`. Casi todo nodo bash re-deriva HU_ID/PLUGIN_ID/BRANCH desde acá vía `jq`.
- **HU_ID** — debe matchear `^HU-[0-9]{8}-[0-9]{4,6}-` (validado en `parse-input`; **más laxo** que el del pipeline principal). Inmutable durante la corrida.
- **BRANCH** — siempre `hu/<HU_ID>`. Debe existir en origin (creada por el orquestador), si no `FAIL_BRANCH_NOT_FOUND`.
- **`$ARTIFACTS_DIR`** — workspace efímero por-corrida; los skills leen/escriben filenames canónicos acá (también es env var real en los bash nodes). Todo lo que escribe el planner y el `task-result.yaml` transient del implementer vive acá hasta que un commit lo haga durable.
- **`$WORKFLOW_ID`** — **no se referencia** en este archivo.
- **`mode` (single/multi_plugin)** — NO se detecta acá. La clasificación vive aguas arriba (tech-refiner / orquestador). Cada invocación maneja exactamente UN `plugin_id`.
- **`MAX_FEATURES_PER_PLUGIN`** — env opcional, default 12. Capea las tasks feature del plugin (chequeado en `validate-feature-plan`).
- **`fresh_context:true`** (loop implementer) — NO se carga contexto entre iteraciones; todo el estado cross-iteración vive SOLO en disco (`task-result.yaml`, `det-retries-<TASK>.count`, `retries-<TASK>.count`, `test-failures.md`, RESULTS_DIR/*-result.yaml, `pipeline-error.yaml`).

**Estrategia de branch (la invariante load-bearing):** `checkout-branch` hace `git checkout --detach origin/$BRANCH` (**HEAD detached, nunca reclama la branch**) precisamente para que N worktrees de sub-pipelines concurrentes puedan apuntar todos a la misma `hu/<HU_ID>` sin la negativa de git 'already checked out'. En consecuencia, **TODO push del archivo es `git push origin HEAD:$BRANCH`** (nunca `git push origin $BRANCH`), en los 3 sitios de push: `commit-feature-plan`, el `until_bash` de `implementar-secuencial`, y `write-plugin-result`. La concurrencia de push inter-pipeline se resuelve con `git pull --rebase origin $BRANCH` + un retry, con warn 'commit local, no en origin' si falla dos veces.

**Smart-resume:** re-lanzar con un HU_ID+plugin existente skipea FASE 1 cuando `feature-plan-manifest` ya está commiteado (vía `detect-resume-feature-plan`) y skipea tasks ya passed (el loop implementer escanea RESULTS_DIR).

## Gotchas y modos de fallo conocidos

- **Detached HEAD + `HEAD:$BRANCH` (fix run 52fa70e0, 2026-05-27).** El checkout detached y los pushes a `HEAD:$BRANCH` existen para soportar N worktrees concurrentes sobre la misma branch. No revertir a `git checkout $BRANCH` / `git push origin $BRANCH`.

- **`all_done` obligatorio en 6 nodos.** Exactamente 6 nodos llevan `trigger_rule: all_done`: `cancel-bad-checkout`, `validate-feature-plan`, `prewarm-uv-venv`, `implementar-secuencial`, `check-pipeline-error`, `write-plugin-result`. Los otros 11 usan `all_success` por defecto. El `all_done` se usa donde un nodo previo puede salir no-cero (o ser best-effort) — con `all_success` el default los skipearía en crash y ocultaría el error real (gotcha #9, run 52fa70e0).

- **Cascading-skip enmascara el error real (lección run 52fa70e0).** Si `checkout-branch` falla y `cancel-bad-checkout` usara `all_success`, se skipearía; entonces `validate-feature-plan` (fan-in `all_done`) correría y emitiría el engañoso `FAIL_NOT_EXISTS`, ruteando a `cancel-bad-feature-plan` — un mensaje de fallo equivocado. El fix dual: `all_done` en el cancel + el guard `when checkout==BRANCH_READY` en `validate-feature-plan` y en `cancel-bad-feature-plan`.

- **Disciplina stdout-single-line (gotcha #8).** Todo nodo gate/bash manda output git y diagnóstico a >&2 y reserva stdout para un único token canónico, porque el condition-evaluator de Archon hace equality estricta. Visible en `commit-feature-plan`, `implementar-secuencial`, `write-plugin-result`.

- **El gate determinista NUNCA confía en el status reportado por el AI.** El `until_bash` re-corre los gates reales **incluso en `passed`/`passed_with_warnings`** (SKILL.md L1164) porque el AI puede mentir o silenciar. Solo commitea código que pasa; en agotamiento de det-retries revierte `src/` y marca `blocked`.

- **No hay 'passed' silencioso sobre trabajo incompleto (fix run b9b95fc5, 2026-05-29).** `write-plugin-result` reporta `failed` si cualquier task planificada carece de result file (completeness check: PLANNED vs produced). Un loop que completó solo F01 de F01/F02/F03 reporta `failed` con `missing_tasks` explícito.

- **`passed_with_warnings` es passing de primera clase (fix run 25512e9, 2026-05-27).** En todos lados (gate, decisión de retry, agregación) `passed_with_warnings` cuenta como passing, para que el `rama-B-merge-batch` del orquestador no se cancele mal.

- **El fallo permanente del implementer sale 0, no crashea.** Escribe `pipeline-error.yaml` y el loop "completa exitosamente". El veredicto fallido se recomputa downstream por `write-plugin-result` — **nunca por un cancel dentro de este sub-pipeline**. `check-pipeline-error` emite `HAS_ERROR` pero es puramente informativo (nada gatea en él).

- **`stage-plugin-context` puede crashear sin cancel dedicado.** `FAIL_PLUGIN_NOT_IN_MANIFEST` levanta `SystemExit` bajo `set -e`; sin nodo cancel guardia, solo se ve como skips en cascada downstream.

- **`PASS_BLOCKED` rutea a cancel.** Un plan con `mode:blocked` produce `PASS_BLOCKED`, pero como todo consumidor keya en `=='PASS'`/`!='PASS'`, cae en `cancel-bad-feature-plan` sin handler distinto de blocked.

- **HU_ID regex más laxo que el pipeline principal.** Acá es `^HU-[0-9]{8}-[0-9]{4,6}-` (solo guion final, sin clase de chars de slug). Un HU_ID que el principal rechazaría podría pasar acá.

- **Contrato de input estricto de 2 tokens.** Pasar 1 token (la forma del pipeline principal) deja `plugin_id` vacío → `cancel-bad-input`. El split es `awk '{print $1}'` / `awk '{print $2}'`.
