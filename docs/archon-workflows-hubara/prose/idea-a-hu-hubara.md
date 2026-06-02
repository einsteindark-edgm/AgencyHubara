## idea-a-hu-hubara — Recorrido narrativo para rediseño

Este es el **entry-point** del pipeline hubara: un front-end "IDEA-to-HU" que toma una idea de negocio cruda y la convierte, en una sola pasada, en un GitHub Issue + tarjeta de Project listos para alimentar al super-orquestador `hu-hubara-pipeline`. Lo que sigue describe una corrida completa, su topología, sus gates, sus caminos de cancelación y los huecos conocidos — todo derivado exclusivamente del modelo verificado.

### 1. Propósito y trigger

**Qué problema resuelve.** Convierte una idea en texto libre (o ruta a un `.md` corto, o texto pegado de un issue existente) en una **HU narrativa de PRODUCTO** bien formada, con estructura exacta:

- `# Título` (≤80 chars, sin punto final)
- bloque `Como / Quiero / Para`
- `## Acceptance criteria` con 3-5 bullets `Given/When/Then`
- `## Out of scope` (≥2 items)
- `## Notas técnicas` (opcional)

El workflow valida la estructura determinísticamente, persiste el draft a `hubara_agency/.hubara/drafts/idea-<ts>.md`, publica un Issue en GitHub con label `hubara-hu`, lo agrega al Project board con status `"Idea refined"` (si hay config), y presenta **una única approval gate**: ¿lanzar `hu-hubara-pipeline` ahora en background?

**Límite de alcance explícito.** Este workflow **NO** produce el refinement TÉCNICO (las 14 secciones + §0 plugin classification) — eso lo hace el pipeline real downstream vía `hubara-tech-refiner-archon`. Acá solo se genera la HU narrativa de producto. Tampoco clasifica `single` vs `multi_plugin`.

**Cómo se invoca.**
```
archon workflow run idea-a-hu-hubara "<idea>"
```
Donde `<idea>` (`$ARGUMENTS` / `$USER_MESSAGE`) es una de tres formas, **sin flags**:
- (a) texto libre de una idea de feature,
- (b) ruta a un `.md` con notas más extensas (detectado con `[ -f "$RAW" ]` y copiado),
- (c) texto pegado de un issue existente (tratado como texto plano).

Config del workflow: `provider: claude`, `model: sonnet`, `interactive: true` (por la approval gate), `worktree.enabled: false`.

**Inputs.**
- `$ARGUMENTS` / `$USER_MESSAGE` — la idea cruda.
- `$ARTIFACTS_DIR` — workspace efímero del run (env var real en bash, y substituido como literal en otros contextos).
- `.archon/github-project-config.yaml` — **OPCIONAL**; si existe habilita el Project sync (`project_number`, `project_owner`, `project_id`, `status_field_id`, `status_options` con `Idea refined`). Lo leen `check-prereqs` (smoke test) y `agregar-a-project`.
- `gh` CLI autenticado con scope `project` + `read:project` (validado por el smoke test de `check-prereqs`).
- `jq` en PATH (validado con `command -v jq`).
- Artefactos intermedios: `$ARTIFACTS_DIR/idea-original.md` (escrito por `normalize-input`), `$ARTIFACTS_DIR/hu-draft.md` (escrito por `refinar-hu-producto`), `$ARTIFACTS_DIR/.issue-url` (escrito por `crear-issue`, leído por los 4 nodos post-issue — **resume-safe**).

El grafo es **lineal con un único fan-out/fan-in** (en la fase de publicación) y **una sola approval gate** al final. 14 nodos, 19 edges.

### 2. Recorrido fase por fase

#### FASE 0 — `prereqs` (`check-prereqs`, `cancel-bad-prereqs`)

`START → check-prereqs`. Corre con `set -e` y valida pre-requisitos de runtime emitiendo un código en stdout (todos los fallos con **exit 0** → routing por VALOR, no por exit code):

1. `gh auth status` → si falla emite `FAIL_GH_AUTH`.
2. `command -v jq` → si no está emite `FAIL_NO_JQ`.
3. Si existe `.archon/github-project-config.yaml`, parsea `project_number` (PN) y `project_owner` (PO) y hace un **SMOKE TEST real** contra el API: `gh project item-list $PN --owner $PO --format json --limit 1`; si falla emite `FAIL_GH_NO_PROJECT_SCOPE`. (El comentario del nodo justifica el smoke test como mejor que parsear `gh auth status`, que cachea scopes.)
4. Si todo pasa emite `OK`. `timeout 30000`.

Es un **gate de valor**. Su salida enruta dos caminos mutuamente excluyentes:
- **Camino OK** → `check-prereqs.output == 'OK'` habilita `normalize-input` (edge `gate`).
- **Camino cancel** → `cancel-bad-prereqs` con `when: $check-prereqs.output != 'OK'`. Es un nodo `cancel:` que aborta el run con tabla de diagnóstico (`FAIL_GH_AUTH → gh auth login`; `FAIL_NO_JQ → brew install jq`; `FAIL_GH_NO_PROJECT_SCOPE → gh auth refresh -s project,read:project`). Como `check-prereqs` siempre termina con exit 0 (success), el `trigger_rule: all_success` se satisface y el `when` discrimina correctamente. Terminal → `END`.

Nota de cobertura: el smoke test de project scope **solo** corre si el config existe Y tiene PN+PO no vacíos; si no, el scope `project` nunca se valida (hueco leve, pero benigno porque `agregar-a-project` skipea limpio cuando no hay config).

#### FASE 1 — `normalize-input` (`normalize-input`, `cancel-bad-input`)

`normalize-input` (gated por `when: $check-prereqs.output == 'OK'`) convierte `$ARGUMENTS` en `$ARTIFACTS_DIR/idea-original.md` con dos guards:

- **Guard 1 (vacío):** trimea whitespace con `tr -d '[:space:]'`; si queda vacío emite `FAIL_EMPTY_INPUT` + exit 0.
- **Detección de tipo de input:** si `[ -f "$RAW" ]` copia ese archivo; si no, hace `echo "$RAW" >` a `idea-original.md`. (Este es el único "modo" interno del workflow — input-type detection, NO clasificación de plugins.)
- **Guard 2 (tamaño mínimo):** `SIZE=$(wc -c < idea-original.md)`; si `SIZE < 20` emite `FAIL_TOO_SHORT_${SIZE}` + exit 0.
- Si todo pasa emite `OK`. `timeout 30000`. (Este nodo **no** tiene `set -e`, pero usa exit 0 explícito en cada guard.)

Gate de valor con dos caminos:
- **Camino OK** → `normalize-input.output == 'OK'` habilita `refinar-hu-producto`.
- **Camino cancel** → `cancel-bad-input` con `when: $normalize-input.output != 'OK'`: nodo `cancel:` que aborta pidiendo una idea no vacía de mínimo 20 chars. Terminal → `END`.

(El `when` sobre `check-prereqs.output == 'OK'` en `normalize-input` es redundante con la dependencia + `cancel-bad-prereqs`, pero está por fail-closed. El umbral de 20 chars acá es distinto del umbral `>200 bytes` que `validate-hu` aplica al draft más adelante.)

#### FASE 2 — `refine` (`refinar-hu-producto`)

`refinar-hu-producto` (gated por `when: $normalize-input.output == 'OK'`) es el **único nodo AI** del workflow: un nodo de prompt LLM (NO bash, NO skill) con `prompt:`, `allowed_tools: [Read, Write]`, `idle_timeout: 600000` (10 min). El prompt instruye actuar como product owner de AgencyHubara:

- **Paso 1:** lee `idea-original.md` (opcionalmente `hubara-architecture-guide` SKILL.md + `sections/01-general.md` si la idea menciona plugin/agente/Temporal).
- **Paso 2:** escribe `hu-draft.md` con la estructura EXACTA (título ≤80 chars sin punto; Como/Quiero/Para; 3-5 AC Given/When/Then; ≥2 out-of-scope; notas técnicas opcionales).
- **Paso 3 (reglas de calidad):** título accionable, narrativa completa, AC empiezan con `Given`, out-of-scope ≥2, doc >200 bytes, **INFERIR lo que falte** (NO preguntar al humano, NO usar placeholders `<TBD>`).

Termina al escribir el archivo — **NO emite output estructurado ni promise**. Es **one-shot**: sin `loop:`, sin `max_iterations`, sin `gate_message`.

**Gotcha de diseño (explícito en el YAML).** Este nodo **NO** usa el skill `hubara-tech-refiner-archon` — produce solo la HU narrativa, no el refinement técnico de 14 secciones. Está tipado como `command` por falta de mejor encaje (es un nodo prompt/AI puro con `allowed_tools`, NO un `.archon/commands/<name>.md` ni un `.claude/skills`).

`refinar-hu-producto → validate-hu` es un edge `sequence` simple (sin `when`).

#### FASE 3 — `validate-draft` (`validate-hu`, `cancel-bad-hu`)

`validate-hu` (sin `when`; corre siempre que `refinar-hu-producto` haya tenido success) valida estructuralmente el draft con early-exit, emitiendo un código por cada fallo (todos exit 0 → routing por valor):

- `test -f` → `FAIL_NOT_EXISTS`
- `wc -c > 200` → `FAIL_TOO_SHORT`
- `grep -qE '^#\s+\S'` → `FAIL_NO_TITLE`
- `grep -qiE 'como\s+'` → `FAIL_NO_NARRATIVE`
- conteo de bullets Given (`grep -cE '^[[:space:]-]*\*?\*?Given'`, default 0); si `<2` → `FAIL_FEW_AC_$ac_count`
- `grep -qiE '^##.*scope'` → `FAIL_NO_SCOPE_SECTION`
- si todo pasa → `PASS`. `timeout 10000`.

Gate estructural con dos caminos:
- **Camino OK** → `validate-hu.output == 'PASS'` habilita `save-draft`.
- **Camino cancel** → `cancel-bad-hu` con `when: $validate-hu.output != 'PASS'`: nodo `cancel:` que aborta con `HU draft validation FAILED: $validate-hu.output` y pide re-lanzar. Terminal → `END`.

Este es **la única red de seguridad** para un draft AI defectuoso. Dos huecos leves: (a) la validación de narrativa busca el literal `como` case-insensitive en cualquier parte del doc, no anclado a la línea narrativa, así que un `como` en otro contexto la satisface; (b) — crítico para el rediseño — `validate-hu` depende con `all_success`, así que **solo corre si `refinar-hu-producto` terminó success** (ver §6).

#### FASE 4 — `persist-draft` (`save-draft`)

`save-draft` (gated por `when: $validate-hu.output == 'PASS'`, `set -e`) persiste el draft validado: `mkdir -p hubara_agency/.hubara/drafts`, construye `DRAFT_PATH=hubara_agency/.hubara/drafts/idea-$(date -u +%Y%m%d-%H%M%S).md`, copia `hu-draft.md` ahí, y emite `DRAFT_PATH` a stdout.

Es el **ÚNICO side-effect del workflow en el working tree** (`worktree.enabled: false`). El draft **NO se commitea** — solo queda en el tree. Comparte dependencia (`validate-hu`) con `cancel-bad-hu`: son ramas mutuamente excluyentes por `when` (`PASS` vs `!=PASS`).

#### FASE 5 — `publish-issue` (`crear-issue`, `agregar-a-project`, `print-issue-info`)

`save-draft → crear-issue` (sequence). **`crear-issue`** (`set -e`) es el nodo más cargado:

- Extrae `TITLE` de la primera línea `# ` del draft (`grep -m1 '^# ' | sed 's/^# //' | head -c 120`); si vacío → `FATAL: no extrajo título` + **exit 1**.
- Crea la label `hubara-hu` (color `0E8A16`) idempotentemente (`gh label create ... || true`).
- `gh issue create --title --body-file hu-draft.md --label hubara-hu`; si RC≠0 → FATAL + tail log + exit 1.
- Parsea `ISSUE_URL` del log (`grep -oE 'https://github\.com/[^ ]+/issues/[0-9]+'`); si vacío → FATAL + exit 1.
- **CRÍTICO:** persiste la URL a `$ARTIFACTS_DIR/.issue-url` (sobrevive al resume post-approval) y la emite a stdout.
- **ÚNICO nodo con `retry`:** `max_attempts 2, delay_ms 5000, on_error transient`. `timeout 60000`.

`crear-issue` hace **fan-out** a dos dependientes:

**`agregar-a-project`** (fail-soft por diseño) — agrega el issue al Project board y le setea status `Idea refined`:
- Si NO existe el config → `echo skipped no_config` + exit 0.
- Lee `ISSUE_URL` de `.issue-url` (fallback a `$create-issue.output`); si vacío → WARN + exit 0.
- Step 1: `gh project item-add`; si RC≠0 y log matchea `missing required scopes|read:project|gh auth refresh` → **`FAIL_GH_SCOPE` + exit 1**; si matchea `already|exists|duplicate` → "item ya estaba"; else WARN.
- Step 2: encuentra `ITEM_ID` con **loop interno de 3 intentos** (`gh project item-list ... | jq` filtrando por `content.url`, `sleep attempt*2`) para el **race con el auto-add** del Project disparado por la label; si no lo encuentra → WARN + exit 0.
- Step 3: extrae `OID` (option_id de `Idea refined`) del config; si vacío → WARN + exit 0.
- Step 4: `gh project item-edit --single-select-option-id $OID`; si RC≠0 → FAIL item-edit + tail + exit 0 (fail-soft). Si OK emite `✅ status 'Idea refined' set`. `timeout 90000`.

La **única excepción a fail-soft** es `FAIL_GH_SCOPE` (exit 1) — ver §4/§6.

**`print-issue-info`** — banner pre-gate. Lee `ISSUE_URL` de `.issue-url` (fallback a output), toma `PROJECT_STATUS=$agregar-a-project.output`, e imprime un banner ASCII (heredoc) con la URL, el Project status y la lista de fases que recorrerá `hu-hubara-pipeline` (Refining → Refined → Planning → Planned → Implementing → Reviewing → Done). `timeout 5000`.

Es **fan-in de 2 deps** (`crear-issue` + `agregar-a-project`) y el **ÚNICO nodo con `trigger_rule` no estándar: `none_failed_min_one_success`** — ningún dep falló Y al menos uno tuvo éxito. Esto le permite correr aunque `agregar-a-project` haya hecho exit 0 con WARN/skip, pero **NO si `agregar-a-project` hizo `FAIL_GH_SCOPE` (exit 1)** → en ese caso `none_failed` es false y `print-issue-info` no corre, cortando la cadena antes del gate.

#### FASE 6 — `launch-gate` (`gate-lanzar-pipeline`, `lanzar-pipeline`, `print-final-summary`)

**`gate-lanzar-pipeline`** es la **ÚNICA approval gate** del workflow (nodo `approval:`, sin `when`). Su `message` muestra: Issue publicado (`$create-issue.output`), Card en `Idea refined`, y la decisión. Describe que APROBAR arranca `hu-hubara-pipeline` en background (~$0.50-1.00, 20-40 min, 7 pasos: refinar técnico → plan plugin-level → single inline / multi fan-out → implementar con gates determinísticos → validación final + PR único → review 5 agentes → Project sync); RECHAZAR termina dejando impreso el comando manual `archon workflow run hu-hubara-pipeline "$create-issue.output"`.

Gate de routing **humano**: el VALOR (approve/reject) decide continue vs stop.
- **Camino approve** → `lanzar-pipeline` corre.
- **Camino reject** → como `lanzar-pipeline` depende con `all_success` y la gate no aprobó, `lanzar-pipeline` no corre, y por cascada `print-final-summary` tampoco → el run termina **sin banner final**, con Issue + card listos para lanzar a mano.

**`lanzar-pipeline`** (sin `when`) dispara el sub-workflow fire-and-forget:
- Lee `ISSUE_URL` de `.issue-url` (fallback a `$create-issue.output`); si vacío → `FAIL_NO_ISSUE_URL` + **exit 1**.
- Valida la URL con `grep -qE '^https://github\.com/.+/issues/[0-9]+'`; si no matchea → `FAIL_INVALID_URL` + **exit 1**.
- Crea `LOG_FILE=$HOME/.archon/logs/hubara-pipeline-<epoch>.log`.
- Lanza en background: `(env -u CLAUDECODE ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1 nohup archon workflow run hu-hubara-pipeline "$ISSUE_URL" > LOG_FILE 2>&1 & disown) 2>/dev/null` y emite `pipeline_triggered_log=$LOG_FILE` (vía `&&`) o `pipeline_trigger_failed_run_manually` (vía `||`). `timeout 10000`.

Es **fire-and-forget**: NO espera ni mergea el resultado del pipeline lanzado (a diferencia de la rama-A inline del pipeline real). NO es una sub-pipeline gestionada por Archon — es un proceso detached. El `env -u CLAUDECODE` + `ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1` evita el "may hang silently" del nested archon-in-claude-code.

**`print-final-summary`** (TERMINAL, sin `when`) lee `ISSUE_URL` de `.issue-url`, toma `PIPELINE=$lanzar-pipeline.output`, e imprime el banner final `🎉 idea-a-hu-hubara completo` con Issue, Pipeline status, y próximos pasos (mirar Project board, esperar status auto, ver PR+comment del review, y el comando manual de fallback si fue `pipeline_trigger_failed_run_manually`). `timeout 5000`. Edge a `END`. Solo corre si `lanzar-pipeline` tuvo success (`all_success`).

### 3. Loops y reintentos

**Loops de iteración: NINGUNO.** Ningún nodo tiene `loop:` block. En particular, `refinar-hu-producto` es **one-shot** — sin `loop:`, sin `max_iterations`, sin `until SIGNAL`, sin `gate_message`. El YAML lo dice explícitamente ("no necesitás emitir promise"). Esta es una diferencia de diseño deliberada respecto del pipeline downstream: el refiner narrativo NO itera con feedback humano. Si el draft queda mal, la recuperación es manual y ocurre **fuera del loop** (ver §6):
1. el operador rechaza `gate-lanzar-pipeline`,
2. edita el Issue directamente en GitHub, o
3. borra el Issue y re-corre el workflow.

**Loop interno (no de workflow):** `agregar-a-project` tiene un **loop bash de 3 intentos** (con `sleep attempt*2`) para encontrar el `ITEM_ID` del Project. Esto NO es un loop de Archon — es lógica interna del bash node para manejar el **race con el auto-add** del Project board que dispara la label `hubara-hu`. Si tras 3 intentos no encuentra el item → WARN + exit 0 (fail-soft, no rompe el run).

**Reintentos (`retry`):** un solo nodo tiene `retry` block — **`crear-issue`**: `max_attempts 2, delay_ms 5000, on_error transient`. Cubre fallos transitorios de red al crear el Issue en GitHub. Ningún otro nodo tiene `retry`.

**Idempotencia GitHub** (no es retry pero mitiga re-corridas): `gh label create ... || true` silencia la label ya existente; `item-add` tolera `already/exists/duplicate`; `item-list` usa el loop de 3 reintentos para el race.

### 4. Caminos de cancelación

Hay **3 nodos `cancel:` dedicados**, todos disparados por `when` negativo (output ≠ valor-OK) y todos con `trigger_rule: all_success` (que se satisface porque el gate upstream siempre termina con exit 0):

| Nodo cancel | `when` exacto | Lo dispara |
|---|---|---|
| `cancel-bad-prereqs` | `$check-prereqs.output != 'OK'` | `FAIL_GH_AUTH`, `FAIL_NO_JQ`, `FAIL_GH_NO_PROJECT_SCOPE` |
| `cancel-bad-input` | `$normalize-input.output != 'OK'` | `FAIL_EMPTY_INPUT`, `FAIL_TOO_SHORT_<N>` |
| `cancel-bad-hu` | `$validate-hu.output != 'PASS'` | `FAIL_NOT_EXISTS`, `FAIL_TOO_SHORT`, `FAIL_NO_TITLE`, `FAIL_NO_NARRATIVE`, `FAIL_FEW_AC_<N>`, `FAIL_NO_SCOPE_SECTION` |

Los tres son terminales → `END`. Son **fail-closed**: cada uno usa `!=` contra el valor OK, así que cualquier salida que no sea exactamente `OK`/`PASS` aborta con diagnóstico. Para los **tres gates de valor** (`check-prereqs`, `normalize-input`, `validate-hu`) la cobertura es **completa por construcción**: el camino continue usa `== 'OK'`/`== 'PASS'` y el camino cancel usa `!= 'OK'`/`!= 'PASS'`, que son mutuamente exclusivos y exhaustivos. No hay silent-hole en estos tres puntos: todo estado de salida matchea exactamente una de las dos ramas.

**Cancelación por `node_error` (sin cancel node dedicado) — patrón de exit code 1.** Tres nodos rompen el patrón "exit 0 + código en stdout" y usan **exit 1 (FATAL)**, que propaga como `node_error` y, al **NO tener cancel node propio**, corta la cadena downstream por `all_success` dejando el run fallado **sin diagnóstico de cancel**:

- **`crear-issue`** — FATAL en: título no extraíble, `gh issue create` RC≠0 (tras el retry), o URL no parseable. Si falla, sus dos dependientes (`agregar-a-project`, `print-issue-info`) no corren.
- **`agregar-a-project`** — exit 1 **solo** en `FAIL_GH_SCOPE`. Esto vuelve `none_failed_min_one_success` de `print-issue-info` en false → `print-issue-info` no corre → la cadena se corta **antes del gate**, sin banner ni approval.
- **`lanzar-pipeline`** — exit 1 en `FAIL_NO_ISSUE_URL` o `FAIL_INVALID_URL`. Si falla, `print-final-summary` (all_success) no corre.

**Silent-holes identificados (riesgos de rediseño):**

1. **`refinar-hu-producto` (AI) que termina en `node_error`/timeout.** `validate-hu` depende con `all_success`, así que si el nodo AI crashea o hace timeout (`idle_timeout: 600000`), `validate-hu` **NO corre** — y no hay ningún cancel node con `trigger_rule: all_done` que capture ese estado. Resultado: **run colgado/fallado sin diagnóstico**. La red de seguridad estructural solo cubre el caso "el AI escribió un archivo malo", NO el caso "el AI no produjo nada". Este es el hueco más serio del grafo.
2. **`crear-issue` FATAL sin cancel dedicado** — corta la cadena por `all_success` sin mensaje de cancel (run fallado en seco).
3. **`agregar-a-project` con `FAIL_GH_SCOPE`** — exit 1 sin cancel dedicado, corta antes del gate. Asimétrico con `check-prereqs`, que SÍ tiene `cancel-bad-prereqs` para el mismo problema de scope detectado upstream.
4. **`check-prereqs` no valida project scope si no hay config** (o si PN/PO están vacíos) — hueco leve, mitigado porque `agregar-a-project` skipea limpio sin config.

### 5. Invariantes y env vars

- **`worktree.enabled: false`** — el workflow **NO** crea branch, **NO** usa `HU_ID`, **NO** usa `BRANCH` ni `WORKFLOW_ID`. El `HU_ID` y la branch `hu/<HU_ID>` los crea el **pipeline downstream** (`hu-hubara-pipeline`). No hay `checkout`/`detach`/`push` en ningún nodo.
- **Estrategia de branch: NINGUNA.** El único write al working tree es `save-draft` → `hubara_agency/.hubara/drafts/idea-<ts>.md`, que **NO se commitea**.
- **`mode` (single/multi_plugin): NO aplica.** Este workflow no clasifica plugins; la clasificación ocurre downstream en `hubara-tech-refiner-archon §0` dentro de `hu-hubara-pipeline`. El único "modo" interno es **input-type detection** en `normalize-input` (`[ -f "$RAW" ]`: archivo vs texto).
- **`$ARTIFACTS_DIR`** — workspace efímero del run; es env var real en bash Y se substituye como literal en otros contextos. Aloja `idea-original.md`, `hu-draft.md` y `.issue-url`.
- **`$create-issue.output`** — la URL del issue; usada en el `message` de la approval gate y como **fallback** de lectura. **NO es confiable post-resume** (ver §6).

**Invariantes run-wide:**
1. **RESUME-SAFETY.** La URL del issue se persiste a `$ARTIFACTS_DIR/.issue-url` porque `$create-issue.output` **se pierde** cuando Archon reanuda tras la approval gate (al reanudar, `prior_success` skipea el nodo pero no preserva stdout). Los **4 nodos post-`crear-issue`** (`agregar-a-project`, `print-issue-info`, `lanzar-pipeline`, `print-final-summary`) leen el archivo con fallback al output.
2. **FAIL-SOFT en Project sync.** `agregar-a-project` y `print-issue-info` casi nunca tumban el run (mayoría exit 0); la única excepción exit 1 es `FAIL_GH_SCOPE` en `agregar-a-project`.
3. **UNA ÚNICA approval explícita** (`gate-lanzar-pipeline`); todo lo demás es automático.
4. **3 cancel nodes con `when` negativo** (output ≠ valor-OK) que abortan con diagnóstico.
5. **Idempotencia GitHub** — `gh label create ... || true`, `item-add` tolera `already/exists/duplicate`, `item-list` con loop de 3 reintentos.
6. **PATRÓN DE EXIT CODES MIXTO** — los validadores (`check-prereqs`, `normalize-input`, `validate-hu`) usan exit 0 + código-en-stdout → routing por VALOR (los cancel nodes disparan por `when`); pero `crear-issue` (FATAL), `agregar-a-project` (solo `FAIL_GH_SCOPE`) y `lanzar-pipeline` (`FAIL_NO_ISSUE_URL`/`FAIL_INVALID_URL`) usan exit 1 → propagan como `node_error` y, sin cancel node dedicado, cortan la cadena por `all_success`. **Esta inconsistencia es un eje central para el rediseño.**

### 6. Gotchas y modos de fallo conocidos

- **`$create-issue.output` se pierde tras la approval gate.** Es el gotcha estructural más importante: cuando Archon reanuda el run después de la decisión humana, el nodo `crear-issue` queda en `prior_success` y se skipea, pero su stdout **no se preserva**. Por eso la URL se materializa a `$ARTIFACTS_DIR/.issue-url` y los 4 downstream leen el archivo con fallback al output. El `message` de la approval gate SÍ puede usar `$create-issue.output` (se renderiza ANTES del resume), pero `lanzar-pipeline` NO puede confiar en él (de ahí la lectura desde `.issue-url`). El **smart-resume del propio `idea-a-hu-hubara`** también depende de esto: si se re-corre, `lanzar-pipeline` ya estaría en `prior_success` y se skipearía — por eso depende del archivo, no del output.

- **`refinar-hu-producto` NO es el skill `hubara-tech-refiner-archon`.** Gotcha de alcance explícito en el YAML: este nodo solo produce la HU narrativa de producto (título + Como/Quiero/Para + AC + out-of-scope). El refinement TÉCNICO (14 secciones + §0 plugin classification) lo hace el pipeline downstream. No confundir los dos al rediseñar.

- **El nodo AI no tiene red de seguridad para "no produjo nada".** `validate-hu` valida el archivo escrito, pero depende con `all_success` — solo corre si `refinar-hu-producto` terminó success. Si el AI hace timeout o crashea (`node_error`), `validate-hu` no corre y **no hay cancel con `all_done`** que diagnostique. El recovery del draft defectuoso es **manual y deliberado** (rechazar gate / editar issue / borrar y re-correr), pero el caso "AI mudo" queda sin captura. **Para el rediseño:** considerar un cancel node con `trigger_rule: all_done` colgando de `refinar-hu-producto`, o validar el output del nodo AI además del archivo.

- **`validate-hu` — narrativa por literal `como`.** La validación de narrativa busca `como` case-insensitive en cualquier parte del documento, sin anclar a la línea `Como ...`. Un `como` incidental en otro contexto satisface el check (hueco leve). Igual, el conteo de AC (`grep -cE '^[[:space:]-]*\*?\*?Given'`) está alineado con el formato `- **Given**` del prompt.

- **Doble umbral de tamaño.** `normalize-input` exige `≥20 chars` al **input crudo**; `validate-hu` exige `>200 bytes` al **draft generado**. Son dos guards distintos en dos puntos distintos — no confundirlos.

- **`agregar-a-project` fail-soft con UNA excepción.** Casi todos sus paths hacen exit 0 (skip sin config, WARN sin URL, item ya existente, item-edit fallido). La **única** salida que rompe el run es `FAIL_GH_SCOPE` (exit 1), que además tiene un efecto en cascada: vuelve `none_failed_min_one_success` de `print-issue-info` en false → la cadena se corta antes del gate. Asimetría notable: el mismo problema de scope detectado upstream por `check-prereqs` SÍ tiene cancel dedicado (`cancel-bad-prereqs`), pero detectado acá no.

- **`print-issue-info` con `trigger_rule: none_failed_min_one_success`** — único `trigger_rule` no estándar del workflow (fuera del set base `all_success`/`all_done`/`one_success`; variante Archon). Permite correr con WARN/skip de `agregar-a-project` (exit 0) pero NO con su exit 1. `PROJECT_STATUS=$agregar-a-project.output` no se persiste a archivo, así que NO sobreviviría a un resume — pero es seguro porque `print-issue-info` corre **PRE-approval** (siempre poblado).

- **Race del auto-add del Project con la label.** El `--label hubara-hu` en `crear-issue` puede disparar un auto-add workflow del Project board; el `item-add` de `agregar-a-project` corre en paralelo a ese auto-add → de ahí el loop de 3 reintentos (`sleep attempt*2`) para resolver el race al buscar el `ITEM_ID`, y la tolerancia a `already/exists/duplicate` en el `item-add`.

- **Fire-and-forget del pipeline lanzado.** `lanzar-pipeline` usa `nohup ... & disown` y NO espera ni mergea el resultado del `hu-hubara-pipeline` lanzado. El éxito del background **nunca** se reporta como exit≠0 (el `&& ... || echo ...` captura el resultado del subshell, no del pipeline). El `env -u CLAUDECODE` + `ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1` es obligatorio para evitar el "may hang silently" del nested archon-in-claude-code.

- **Doc-vs-YAML mismatch en `check-prereqs`.** El README §7 menciona `FAIL_MISSING_*`/`FAIL_DIRTY_PROTECTED_FILES` para `check-prereqs`, pero esos códigos **NO existen** en este YAML (son de `hu-hubara-pipeline`). Al rediseñar, no asumir esos códigos: los únicos reales acá son `OK`/`FAIL_GH_AUTH`/`FAIL_NO_JQ`/`FAIL_GH_NO_PROJECT_SCOPE`.
