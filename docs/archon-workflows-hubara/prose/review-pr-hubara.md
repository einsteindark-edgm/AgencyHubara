I'll review the verified model and produce the narrative walkthrough. Let me check one detail in the model regarding the trigger_rule count to ensure accuracy.

The model is the source of truth. Here's the narrative walkthrough.

## review-pr-hubara — Recorrido narrativo para rediseño

### 1. Propósito y trigger

`review-pr-hubara` es un **code review automatizado POST-PR, multi-agente y NO bloqueante**. Audita PRs producidos por `hu-hubara-pipeline` (o invocados a mano sobre cualquier PR), corre hasta 5 agentes especialistas en paralelo, consolida hallazgos, intenta auto-arreglar lo CRITICAL/HIGH, y deja un comentario informativo en el PR. **El operador decide si mergea o itera** — el workflow nunca bloquea el merge.

**Invocación exacta:**

```bash
archon workflow run review-pr-hubara "<PR_URL>"
```

- Un único argumento posicional `$ARGUMENTS` = la URL del PR, que debe matchear `^https://github\.com/.+/pull/[0-9]+$`.
- También se auto-dispara en background desde `hu-hubara-pipeline.trigger-review`.
- Config top-level: `provider: claude`, `model: sonnet`, `interactive: false`, `worktree.enabled: true`.

**Inputs:**

- `$ARGUMENTS` = la URL del PR.
- Prereqs de runtime: `gh` autenticado, `jq` presente, `.claude/skills/hubara-architecture-guide/SKILL.md` presente.
- Remoto `origin` de git con las branches head + base del PR fetchables.
- Secciones del `hubara-architecture-guide` (02–09) + referencias (`deha-rules.md`, `fsd-rules.md`, `manifest-schema.md`), leídas por los 5 agentes vía `Read`.
- Opcional: `.archon/github-project-config.yaml` para el update de estado 'Reviewing' del Project.
- El body del PR conteniendo `Closes <issue-url>` para que `project-set-reviewing` localice el item del proyecto.

El total es de **20 nodos** organizados en 6 fases.

---

### 2. Recorrido fase por fase de una corrida

#### FASE 0 — Bootstrap (parse + prereqs + checkout + diff)

Nodos: `parse-input`, `cancel-bad-input`, `check-prereqs`, `cancel-bad-prereqs`, `fetch-pr`, `checkout-branch`, `fetch-diff`.

1. **`parse-input`** (entry, sin `depends_on`). Lee `PR_URL=$ARGUMENTS`. Si NO matchea el regex, hace `echo 'FAIL_BAD_URL: <url>'` y `exit 0` (no fatal). Si matchea, extrae el número de PR (`grep -oE '[0-9]+$'`), escribe `$ARTIFACTS_DIR/pr-url.txt` y `pr-num.txt`, y emite `OK`. `timeout 5000ms`. Es un **gate**: su valor de salida enruta la bifurcación.
   - **Camino cancel:** `cancel-bad-input` dispara con `when: $parse-input.output != 'OK'` → cancela todo el workflow con el mensaje *"Input inválido. Usage: archon workflow run review-pr-hubara '<PR_URL>'"*.
   - **Camino OK:** `check-prereqs` corre con `when: $parse-input.output == 'OK'`.

2. **`check-prereqs`** (gate). Verifica en orden: `gh auth status >&2 2>&1` (→ `FAIL_GH_AUTH`), `command -v jq` (→ `FAIL_NO_JQ`), `test -f .claude/skills/hubara-architecture-guide/SKILL.md` (→ `FAIL_MISSING_GUIDE_SKILL`). Si todo pasa, `OK`. Cada chequeo hace `exit 0` con un token `FAIL_*` (no exit no-cero), difiriendo el aborto al cancel node. La salida de `gh` va a stderr para mantener limpia la línea canónica.
   - **Camino cancel:** `cancel-bad-prereqs` dispara con `when: $check-prereqs.output != 'OK'` → cancela con el mensaje *"Pre-requisitos: $check-prereqs.output. (gh auth login / brew install jq / commit guide skill)"*, interpolando el token `FAIL_*`.
   - **Camino OK:** `fetch-pr` corre con `when: $check-prereqs.output == 'OK'`.

3. **`fetch-pr`** (`set -e`). Lee `PR_NUM` de `pr-num.txt`, hace `gh pr view PR_NUM --json title,body,baseRefName,headRefName,files > pr.json`. Extrae `BRANCH=jq .headRefName`, `BASE=jq .baseRefName`, emite `branch=<B> base=<B>`, escribe `branch.txt` + `base.txt`. **No hay cancel node que lo guarde**: si `gh`/`jq` fallan, el nodo aborta no-cero y los deps `all_success` downstream simplemente no disparan (halt silencioso, sin mensaje de cancel).

4. **`checkout-branch`** (`set -e`). Lee `BRANCH` de `branch.txt`; `git fetch origin BRANCH`; `git checkout BRANCH`; `git pull --ff-only origin BRANCH` (output a stderr, con `|| true` para que un pull no-ff no aborte). Emite `OK`. Es un **FULL checkout** que reclama la branch (NO detached HEAD). Seguro porque corre en su propio worktree.

5. **`fetch-diff`** (sin `set -e`). Lee `BASE` de `base.txt`. Escribe `git diff origin/BASE...HEAD` → `diff.patch` y `git diff --name-only origin/BASE...HEAD` → `files-changed.txt` (tres puntos = merge-base). Emite `files=<count>`. Si `origin/BASE` no resuelve, `git diff` falla pero el nodo igual sale `exit 0` con un diff posiblemente vacío. Estos dos artifacts alimentan al clasificador y a los 5 agentes.

#### FASE 1 — Classify (haiku selecciona agentes)

Nodos: `classify`, `parse-classify`.

6. **`classify`** (skills, `model: haiku`, `idle_timeout 60000`). Loop node con `skills: []` vacío (prompt puro, `gate_message 'Classifier auto.'`). Lee `files-changed.txt` y escribe `agents-to-run.json = {deha,fsd,plugin_system,test_coverage:true,security}`. Reglas: `deha` si hay `hubara_agency/src/`; `fsd` si hay `frontend_dashboard/src/`; `plugin_system` si hay `plugin.yaml`/`plugin.schema.yaml`/`plugin_manifest.py`/`scripts/render-compose.py`/`k8s/aws-produccion/`; `test_coverage` SIEMPRE true; `security` si hay `.env*`/`secrets`/`configmap.yaml` o Python que agrega `os.environ`/`getpass`. Emite `<promise>CLASSIFY_OK</promise>`.

7. **`parse-classify`** (bash, **gate**, `trigger_rule: all_done`). `F=$ARTIFACTS_DIR/agents-to-run.json`. Si está ausente, escribe el default (todo true incl. `test_coverage`). Lee `DEHA=jq '.deha//false'`, `FSD=jq '.fsd//false'`, `PS=jq '.plugin_system//false'`, `TC=jq '.test_coverage//true'`, `SEC=jq '.security//false'`, y emite un objeto JSON vía `jq -n --argjson ...` para que Archon exponga `output.deha/fsd/plugin_system/test_coverage/security` como campos estructurados. Es el **selector fan-out** del workflow. El `trigger_rule: all_done` es crítico: `classify` puede agotar sus 2 loops sin `CLASSIFY_OK` (no-success) y aun así `parse-classify` debe disparar para defaultear los agentes.

#### FASE 2 — 5 agentes especialistas en paralelo

Nodos: `agent-deha-compliance`, `agent-fsd-compliance`, `agent-plugin-system`, `agent-test-coverage`, `agent-security`.

Los cinco son loop nodes (`skills: []`, `idle_timeout 300000`), dependen SOLO de `parse-classify` (corren en paralelo), cada uno **gated** por su flag de clasificador comparado contra el STRING `'true'`, y cada uno lee SOLO sus secciones relevantes del `hubara-architecture-guide` + `diff.patch` + `files-changed.txt`. Cada uno emite `findings-<agent>.yaml` y señaliza `<promise>REPORT_OK</promise>`. Todos default `trigger_rule: all_success`. Si su flag no está seleccionado, el agente se skipea.

- **`agent-deha-compliance`** (`when: $parse-classify.output.deha == 'true'`). El prompt más rico (~240 líneas). Lee secciones 02/03/04-backend + `references/deha-rules.md`. Caza R-DET/R-JSON/R-STATELESS/R-HEARTBEAT/R-DIP, incluyendo el patrón CRITICAL de import cross-agent (ADR-2026-05-20 #10) y `start_workflow` sobre la workflow class de un agente hermano; más footguns F1 (dict→dataclass contract drift, HIGH), F2 (workflow.patched branch parity, MEDIUM), F3 (Path.resolve, LOW), F4 (bootstrap runtime_workspace_path fallback, MEDIUM), F5 (nested dataclass + PEP 563 en activity return, HIGH `merge_blocking`), F7 (start_delay sin eligibility gate, HIGH `merge_blocking`). Solo R-DIP cross-agent y F5/F7 marcan `merge_blocking`.
- **`agent-fsd-compliance`** (`when: ...fsd == 'true'`). Lee secciones 05/06-frontend + `references/fsd-rules.md`. Caza los 14 anti-patterns + 4 import rules: imports cross-plugin, deep imports salteando el barrel, `fetch()` directo en components/pages, `useState` para server data, `apiClient.get<T>()` sin `schema.parse()`, JSX en `.ts`, imports cross-feature, naming Tailwind `--color-text-*`, env vars hardcodeadas fuera de `shared/config/env.ts`, violaciones de layering. Sin footguns `merge_blocking`.
- **`agent-plugin-system`** (`when: ...plugin_system == 'true'`). Lee secciones 07/08 + `references/manifest-schema.md`. Verifica schema de `plugin.yaml` (id `^[a-z][a-z0-9_]*$`, SemVer, workers con `task_queue`), correspondencia worker↔manifest K8s (`worker-<name>.yaml` en `k8s/aws-produccion/`), sin manifests K8s huérfanos, unicidad de `task_queue` cross-plugin, drift de render-compose, plugin id == nombre de directorio, `wiring_intents` de shared-files, archivos architecture-protected NO modificados. Footgun F7 'frontend block contract': manifest declara `frontend:` → `<plugin>/frontend/index.ts` DEBE existir (HIGH `merge_blocking`); no declarado → el dir NO debe existir (MEDIUM); tocar `plugin.yaml`/frontend exige regenerar `src/app/plugin-registry.generated.ts` (LOW).
- **`agent-test-coverage`** (`when: ...test_coverage == 'true'`). Lee sección 08. Verifica que cada feature nueva tenga un test funcional en `hubara_agency/tests/functional/` con `@pytest.mark.functional` (`mock_llm` si LLM); cambios de UI con specs e2e en `frontend_dashboard/e2e/<feature>/` usando `getByRole`/`getByText` (sin `waitForTimeout`); gates de arquitectura + invariantes de premortem pasando; unit tests por cada tool/activity/hook nuevo; skips de refactor puro documentados en `task-result`. **Verifica PRESENCIA de tests, no comportamiento.**
- **`agent-security`** (`when: ...security == 'true'`). Lee secciones 02-backend + 09-conventions. Caza secretos hardcodeados (`AKIA[A-Z0-9]{16}`, `ghp_*`, JWT `ey*.*.`, passwords), env vars nuevas no declaradas en `wiring_intents.env_vars_required`, `os.environ` cruzando boundary, logs exponiendo secretos (body inbound de WhatsApp sin sanitizar), CORS demasiado permisivo, validación de input HTTP faltante (sin Pydantic/Zod), secretos K8s no rotables (hardcoded vs `valueFrom`). Sin footguns `merge_blocking`.

#### FASE 3 — Synthesize hallazgos + decide auto-fix

Nodo: `synthesize`.

8. **`synthesize`** (skills, loop, `idle_timeout 180000`, `trigger_rule: all_done`). Es el **fan-in** de los 5 agentes; el `all_done` tolera agentes skipeados/fallidos y aun así corre, debiendo manejar findings files ausentes. Lee cada `findings-*.yaml` presente y produce tres outputs:
   - **`review-report.md`**: conteos de severidad + por-agente + hallazgos agrupados + lista 'Auto-fix attempted'.
   - **`auto-fix-plan.yaml`** = `fixes[]` (SOLO critical+high con `fix_suggestion` no vacío), cada uno `{file, severity, rule, patch (unified diff), revertible_by_test}`.
   - **`merge-decision.yaml`** = `{blocked:bool, blocking_findings[]}`.
   
   Reglas especiales: cualquier hallazgo R-DIP cuyo mensaje contenga 'Cross-agent'/'sibling'/'viola R-DIP #10' → se sube a critical + `merge_blocking:true`; los hallazgos F1 dict→dataclass HIGH → se agregan a `blocking_findings`. Emite `<promise>SYNTH_OK</promise>`.

#### FASE 4 — Auto-fix CRITICAL/HIGH (revert si rompe el test)

Nodos: `auto-fix`, `commit-fixes`.

9. **`auto-fix`** (bash, `timeout 1800000ms` = 30 min, default `all_success`). Inicializa `fixes-applied.yaml` y `fixes-reverted.yaml` a `'fixes: []'`. Si `auto-fix-plan.yaml` está ausente → `echo 'no_plan'; exit 0`. Si no, un heredoc inline de `python3` carga el plan e itera `fixes[]`; por cada uno: snapshot con `git show HEAD:<file>`, escribe el patch a un tempfile, `git apply --check` (falla → reverted + `'patch_did_not_apply'`), luego `git apply`. Si hay `revertible_by_test`, lo corre (`timeout 300s`) — si sale no-cero, `git checkout HEAD -- <file>` y registra `'test_failed_after_fix'`. Los exitosos van a `applied[]`. Dumpea vía `os.environ.get('APPLIED_PATH','$ARTIFACTS_DIR/...')` con fallback de path literal. Imprime `applied=<n> reverted=<m>`.

10. **`commit-fixes`** (bash, `trigger_rule: all_done`). Lee `BRANCH` de `branch.txt`. `git add -A` (`|| true`). Si `git diff --staged --quiet` (nada staged) → `echo 'no_fixes_to_commit'; exit 0`. Si no, `N` = conteo de fixes de `fixes-applied.yaml` (yq, fallback python3 yaml, sino `'?'`); `git commit -m "review-pr-hubara: auto-fix ${N} critical/high finding(s)"`; `git push origin BRANCH` con fallback `git pull --rebase origin BRANCH` + retry-push (diagnósticos → stderr). Emite `fixes_committed=<N>`. Pushea la ref FULL de branch (`git push origin BRANCH`, no `HEAD:BRANCH`) porque `checkout-branch` reclamó la branch. `$ARTIFACTS_DIR` está fuera del worktree, así que `git add -A` solo stagea fixes de código real.

#### FASE 5 — Post comment + project status + summary

Nodos: `post-comment`, `project-set-reviewing`, `print-summary`. Los tres usan `trigger_rule: all_done` para que la corrida termine limpia aun cuando pasos upstream fueran skipeados o parcialmente fallidos.

11. **`post-comment`** (bash, `all_done`). Lee `PR_URL` de `pr-url.txt`; `REPORT=$ARTIFACTS_DIR/review-report.md`. Si el reporte está ausente → `echo 'FAIL_NO_REPORT'; exit 0` (completion parcial silenciosa, sin cancel). Si no, construye `comment.md`: un header '🤖 Automated Review', el reporte completo, un bloque 'Auto-fix summary' (`N_APPLIED`/`N_REVERTED` contados de `fixes-applied.yaml`/`fixes-reverted.yaml` vía python3, fallback `'?'`) si `fixes-applied.yaml` existe, y un footer. Postea vía `gh pr comment PR_URL --body-file comment.md` (output → stderr). Emite `comment_posted`. **El comentario es informativo y NO bloqueante; NO lee `merge-decision.yaml`.**

12. **`project-set-reviewing`** (bash, `all_done`, best-effort). Si `.archon/github-project-config.yaml` está ausente → `echo 'skipped'; exit 0`. Lee `PR_NUM`, deriva `ISSUE_URL` del body del PR (`gh pr view --jq .body | grep -oE 'Closes https://[^ ]+' | head -1 | awk '{print $2}'`); si no hay → `'no_issue_url_in_pr'`. Parsea `project_number`/`owner`/`id`/`status_field_id` y el option id de 'Reviewing' (awk); si no hay option → `'no_reviewing_option'`. Encuentra el item id del proyecto que matchea la URL del issue vía `gh project item-list ... | jq`; si no hay → `exit 0`. Corre `gh project item-edit` para setear el campo single-select. Emite `'set Reviewing ok'`. Todo path de falla sale `exit 0` con un token de status.

13. **`print-summary`** (bash, `timeout 5000ms`, nodo terminal). Depende de AMBOS `post-comment` Y `project-set-reviewing` con `trigger_rule: all_done`. Lee `PR_URL` de `pr-url.txt` e imprime una caja ASCII: '🎉 review-pr-hubara completo', la URL del PR, una nota de que el comentario fue posteado con hallazgos consolidados + auto-fixes aplicados (revertidos si rompían tests), y next steps (revisar comentario, arreglar pendientes a mano, squash-merge cuando esté OK). Imprime sin importar si los pasos previos tuvieron éxito.

---

### 3. Loops y reintentos

Hay **7 loop nodes**, todos con la misma forma `max_iterations: 2, until: <SIGNAL>`:

| Nodo | Señal de cierre (`until`) | Tipo |
|---|---|---|
| `classify` | `CLASSIFY_OK` | haiku, prompt inline |
| `agent-deha-compliance` | `REPORT_OK` | skills inline |
| `agent-fsd-compliance` | `REPORT_OK` | skills inline |
| `agent-plugin-system` | `REPORT_OK` | skills inline |
| `agent-test-coverage` | `REPORT_OK` | skills inline |
| `agent-security` | `REPORT_OK` | skills inline |
| `synthesize` | `SYNTH_OK` | skills inline |

El `max_iterations: 2` es una **red de seguridad de reintento**: el agente a veces termina la iteración 1 sin emitir la promise (caso observado: run 2484bd91, `agent-plugin-system` — este caso motivó el `max_iterations:2` en TODOS los loop nodes). El 2º pase deja que la emita; es no-op si ya estaba hecha.

**Qué pasa si NO se emite la señal de completion:**

- **`classify` sin `CLASSIFY_OK`:** agota sus 2 loops (estado no-success). Sin embargo, `parse-classify` tiene `trigger_rule: all_done`, así que igual dispara y **defaultea todos los agentes a true** (incl. `test_coverage`). Robusto.
- **Cualquier agente sin `REPORT_OK`:** el agente queda en no-success. `synthesize` tiene `trigger_rule: all_done`, así que corre igual y maneja el `findings-<agent>.yaml` ausente. Robusto.
- **`synthesize` sin `SYNTH_OK`:** queda no-success. Aquí está el **riesgo**: `auto-fix` tiene default `trigger_rule: all_success`, así que se **SKIPEA** → cascade. `commit-fixes` y `post-comment` usan `all_done`, así que igual disparan, pero **sin reporte** (`post-comment` emite `FAIL_NO_REPORT` y no postea nada). Completion parcial silenciosa.

---

### 4. Caminos de cancelación

Hay **exactamente DOS** nodos cancel, ambos pre-diff en FASE 0:

| Nodo cancel | Condición exacta (`when`) | Mensaje |
|---|---|---|
| **`cancel-bad-input`** | `$parse-input.output != 'OK'` | "Input inválido. Usage: archon workflow run review-pr-hubara '<PR_URL>'" |
| **`cancel-bad-prereqs`** | `$check-prereqs.output != 'OK'` | "Pre-requisitos: $check-prereqs.output. (gh auth login / brew install jq / commit guide skill)" |

Ambos tienen `trigger_rule: all_success` por default; como `parse-input` y `check-prereqs` SIEMPRE salen `exit 0` (son "success"), el cancel node es siempre elegible y el `when` es el verdadero guard del routing. **Después del bootstrap NO hay ningún path de cancel/abort** — la review es completamente informativa.

**Cobertura de los gates — riesgo de silent-hole:**

Los dos gates de bootstrap (`parse-input`, `check-prereqs`) SÍ cubren todos sus estados: emiten exactamente `OK` o un token `FAIL_*`, y cada salida `!= 'OK'` matchea el cancel mientras `== 'OK'` matchea el continuar. Las particiones son completas.

Pero el modelo identifica **silent-holes reales más abajo, no por gates incompletos sino por ausencia de guards de cancel sobre nodos `set -e`:**

- **`fetch-pr`** y **`checkout-branch`** usan `set -e` y **NO tienen cancel node que los guarde**. Si `gh`/`jq` (en `fetch-pr`) o el `git fetch`/`git checkout` (en `checkout-branch`) fallan, el nodo aborta no-cero y los deps `all_success` downstream simplemente no disparan: **halt silencioso, sin token `FAIL_*`, sin mensaje de cancel**. Gap de observabilidad.
- **SILENT HOLE de alto valor (`merge-decision.yaml`):** `synthesize` PRODUCE `merge-decision.yaml` (`{blocked, blocking_findings[]}`) y su prompt (líneas 620-621) AFIRMA *"la fase final del workflow lee este artifact y bloquea el merge si blocked=true"*. Pero **NINGÚN nodo downstream lee `merge-decision.yaml`** (verificado: ni `auto-fix`, ni `commit-fixes`, ni `post-comment`, ni `project-set-reviewing`, ni `print-summary` lo leen). Toda la maquinaria de `merge_blocking` (R-DIP cross-agent, F5, F7, F1) se computa y se "bumpea" pero **nunca se enforza**. Las líneas 28-29 confirman: *"El comment NO bloquea el PR — es informativo."* Es maquinaria muerta.
- **`post-comment` con reporte ausente:** emite `FAIL_NO_REPORT`, `exit 0`, no postea — completion parcial silenciosa, sin cancel.

---

### 5. Invariantes y env vars

- **HU_ID / modo single-vs-multi_plugin:** **NO existe en ESTE workflow.** Esa lógica vive en `hu-hubara-pipeline`. El selector run-wide acá es la salida del clasificador haiku: `agents-to-run.json = {deha:bool, fsd:bool, plugin_system:bool, test_coverage:true(siempre), security:bool}`, parseado por `parse-classify` a campos JSON estructurados que Archon expone como `output.deha/fsd/...`. Cada agente se gatea con `when: $parse-classify.output.<field> == 'true'` (compara el booleano JSON renderizado como el STRING `'true'`).
- **`$ARGUMENTS`:** la URL del PR.
- **`$ARTIFACTS_DIR`:** workspace efímero por-corrida; es a la vez sustitución de texto literal Y una env var real para los bash nodes. Está **fuera del worktree**, por eso `git add -A` en `commit-fixes` stagea solo fixes de código real.
- **`BRANCH` / `BASE`:** **NO son env vars del workflow.** Se derivan en runtime de `pr.json` (`jq .headRefName` / `jq .baseRefName`) y se persisten a `$ARTIFACTS_DIR/branch.txt` + `base.txt`. (`pr-url.txt` + `pr-num.txt` los persiste `parse-input`.)
- **`WORKFLOW_ID`:** no aparece en el modelo; no se afirma.
- **Estrategia de branch:** **FULL checkout** de la branch head del PR (`git fetch origin BRANCH`; `git checkout BRANCH`; `git pull --ff-only`) — **NO detached HEAD** (a diferencia de los sub-pipelines de plugin, gotcha #9 del proyecto). Es seguro porque la review corre como standalone en su propio worktree. El diff se computa `git diff origin/BASE...HEAD` (tres puntos = merge-base). Los commits de auto-fix aterrizan en la MISMA branch del PR vía `git push origin BRANCH` con fallback `pull --rebase + retry`; la concurrencia entre pushes la maneja ese retry.
- **Invariante de cancel:** solo DOS cancel nodes (`cancel-bad-input`, `cancel-bad-prereqs`), ambos pre-diff en FASE 0. Después del bootstrap ningún gate cancela la corrida.
- **Convención de bash status nodes:** status canónico de una sola línea en stdout (`OK` / `FAIL_*` / `no_plan` / `no_fixes_to_commit` / `skipped` / etc.), diagnósticos a stderr (`>&2`). Varios bash nodes usan `exit 0` incluso en falla (emitiendo un token `FAIL_*`) para que los nodos `all_done` downstream igual disparen; solo los dos cancel nodes explícitos abortan.

---

### 6. Gotchas y modos de fallo conocidos

1. **`merge-decision.yaml` es maquinaria muerta (el gotcha más caro).** Se produce y el prompt promete que bloquea el merge, pero nadie lo lee. Un rediseño debe O BIEN cablear un gate que lea `blocked:true` y cancele/marque, O BIEN borrar la generación de `merge-decision.yaml` y corregir el prompt para no mentir. Hoy el "merge_blocking" de R-DIP cross-agent / F5 / F7 / F1 no tiene efecto alguno.

2. **`fetch-pr` y `checkout-branch` fallan en silencio.** `set -e` sin cancel guard → un fallo de `gh`/`git` detiene el workflow sin mensaje. Si una corrida "no hace nada", sospechar de estos dos. Candidatos a envolver con token `FAIL_*` + cancel node, igual que el bootstrap.

3. **`agent-test-coverage` tiene un `when` pese a ser "siempre".** Depende del default `jq '.test_coverage // true'` de `parse-classify`, que solo dispara cuando la key está ausente/null. Si el clasificador escribiera explícitamente `test_coverage:false`, el agente se **skipearía en silencio**. El "always" es frágil.

4. **`agent-test-coverage` verifica PRESENCIA, no comportamiento** (gotcha #1 del proyecto). Chequea que existan tests, no que el backend EMITA los datos. No confiar en este agente para atrapar features rotas con tests verdes.

5. **Colisión de nombres en footgun "F7".** Hay DOS F7 distintos: en `agent-deha-compliance` F7 = 'Workflow con start_delay sin eligibility gate' (HIGH `merge_blocking`); en `agent-plugin-system` F7 = 'Frontend block contract' (HIGH `merge_blocking`). Mismo label, agentes y significados distintos. Confunde al cross-referenciar hallazgos.

6. **`auto-fix`: el bloque `env:` del nodo NO llega al subproceso python3.** `os.environ.get('APPLIED_PATH')` → `None` → `open(None)` lanza `TypeError`. El fix vigente es el fallback de path literal `'$ARTIFACTS_DIR/...'` que Archon sustituye por texto (mismo patrón en `PLAN_PATH`/`APPLIED_PATH`/`REVERTED_PATH`). Es un footgun conocido (run 2484bd91); cualquier refactor del heredoc debe preservar ese fallback literal.

7. **`auto-fix`: revert por archivo completo → hazard de orden con múltiples fixes al mismo archivo.** El revert es `git checkout HEAD -- <file>`, que revierte el ARCHIVO ENTERO. Si hay dos fixes al mismo archivo y el segundo rompe su test, el revert descarta TAMBIÉN el primer fix ya aplicado a ese archivo. Un rediseño debería revertir por hunk o reordenar/agrupar fixes por archivo.

8. **Si `synthesize` no emite `SYNTH_OK`, `auto-fix` se skipea (cascade).** `auto-fix` es `all_success`; un loop agotado lo skipea. `commit-fixes` y `post-comment` (`all_done`) igual disparan pero sin plan ni reporte → `post-comment` emite `FAIL_NO_REPORT` y no postea. Completion parcial silenciosa.

9. **`project-set-reviewing` depende del body del PR.** Necesita la línea `Closes <issue-url>` (la produce `build-pr-body` de `hu-hubara-pipeline`). PRs revisados a mano sin esa línea → `'no_issue_url_in_pr'` y ningún update del proyecto. Todo es best-effort: cada falla sale `exit 0` con un token de status.

10. **`commit-fixes` respeta la convención stderr correctamente** (gotcha #8 del proyecto): diagnósticos a `>&2`, stdout single-line. Pushea la ref FULL de branch porque `checkout-branch` la reclamó. Sirve de patrón de referencia para los demás bash nodes.
