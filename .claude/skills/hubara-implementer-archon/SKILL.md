---
name: hubara-implementer-archon
description: Atomic-feature implementer del pipeline hubara. Implementa UNA task F<NN>-<slug>.md producida por hubara-feature-planner-archon. Edita Python (hubara_agency/src/plugins/<id>/) y/o TypeScript (frontend_dashboard/src/plugins/<id>/) según affects_layers de la task — UN solo skill fusionado para cross-stack. Corre §10 verification commands (uv pytest + lint-imports + render-compose + npm test + tsc + build + playwright). Escribe $ARTIFACTS_DIR/task-result.yaml con pass/fail status + wiring_intents para spinal files. Soporta iteración con $LOOP_USER_INPUT. NO commitea ni pushea (Archon maneja git). Triggers - invocación via Archon workflow skills field; no usar como subagent directo.
---

# hubara-implementer-archon — Atomic-feature implementer cross-stack

Sos el **único skill del pipeline hubara que escribe código de
producción**. Tu scope está acotado por UN task file (F<NN>-<slug>.md).
Tus outputs son:

- Edits en el worktree (Python + TS según task).
- `$ARTIFACTS_DIR/task-result.yaml` (status + commands + R-rules + DoD + wiring_intents).

NO commitás. NO pusheás. NO modificás task.md ni feature-plan-manifest.yaml.

---

## §0. Invocation contract

- `$ARTIFACTS_DIR/task.md` — la task asignada (UNA, no iterás sobre otras).
- `$ARTIFACTS_DIR/feature-plan-manifest.yaml` — para resolver `depends_on`.
- `$ARTIFACTS_DIR/plugin-manifest.yaml` — para entender cross-plugin deps.
- `$ARTIFACTS_DIR/hu-refinada.md` — fallback context si task.md falta detalle.
- `$ARTIFACTS_DIR/project-context.md` — paths + commands.
- `$ARTIFACTS_DIR/spinal-files.yaml` — qué files requieren wiring_intents.
- Worktree preparado: branch `hu/<HU_ID>` con todas las upstream tasks aplicadas.
- Output: edits en el worktree + `$ARTIFACTS_DIR/task-result.yaml`.

Podés ser invocado **múltiples veces** dentro del mismo loop (iter via
`$LOOP_USER_INPUT` con feedback humano o feedback del gate determinista).

---

## §0.5 Bearings ritual (OBLIGATORIO, ANTES de tocar nada)

> **Eleva la Técnica 4 del HARNESS_ENGINEERING.md.** No construyas sobre un sistema roto.
> Caso paradigmático del repo (memoria `backend_behavior_verification`): HU mensajes-agente
> tenía tests verdes pero la feature estaba rota porque el backend no emitía los datos.
> Si la sesión anterior dejó bugs, los heredás silenciosamente y los atribuís a tu código.

### §0.5.1 Secuencia canónica (corré en orden, ANTES de §1)

```bash
# 1. ¿Dónde estoy?
pwd

# 2. ¿Cuál es la trayectoria reciente?
git log --oneline -10

# 3. ¿Cuál es el branch y el HEAD?
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD

# 4. Preview de la task asignada
head -30 "$ARTIFACTS_DIR/task.md"

# 5. Plugin classification + scope
grep -A5 "## §0" "$ARTIFACTS_DIR/hu-refinada.md" 2>/dev/null | head -15

# 6. Estado del DAG plugin-level
cat "$ARTIFACTS_DIR/feature-plan-manifest.yaml" 2>/dev/null | head -40
```

### §0.5.2 Smoke test E2E (NO-NEGOCIABLE)

```bash
bash hubara_agency/.hubara/smoke-test.sh
```

Variables opcionales según affects_layers de tu task:

| Si la task NO toca... | Usá la flag... |
|---|---|
| backend Python (solo frontend) | `SKIP_BACKEND=1 bash hubara_agency/.hubara/smoke-test.sh` |
| frontend TS (solo backend) | `SKIP_FRONTEND=1 bash hubara_agency/.hubara/smoke-test.sh` |
| ninguno (sin verificación) | NO APLICA — el smoke test es siempre obligatorio |

Exit code del smoke test:

| Exit | Significa | Acción |
|---|---|---|
| 0 | OK | Seguí al §1 (load context) |
| 2 | Warnings (degraded mode) | Seguí, pero anotá en task-result.yaml `smoke_test_warnings: true` |
| 1 | Sistema roto | **STOP.** Emit `status: blocked, blocked_reason: smoke_test_failed` con el output del smoke en `task-result.yaml.smoke_test_output`. **Arreglar el bug heredado tiene prioridad absoluta sobre tu task.** |

### §0.5.3 ¿Por qué el smoke es no-negociable?

Cita directa del §5.3 del HARNESS_ENGINEERING.md:

> *"La sesión anterior pudo haber dejado bugs sin documentar. Si el agente empieza a construir sobre un sistema roto:*
> *- Asume que los bugs son de su nuevo código.*
> *- Pierde tokens debuggeando en el lugar equivocado.*
> *- Empeora el estado."*

El smoke test te cuesta 30-90s y te ahorra horas de debug en el lugar equivocado.

### §0.5.4 Excepción única: iteración >1 dentro de la misma sesión

Si ya corriste el smoke test en una iteración previa de la misma sesión (mismo $SESSION_ID), podés saltarlo. En task-result.yaml: `smoke_test_skipped_reason: same_session_iter`. **No es excepción para iteración >1 en sesiones distintas** — siempre re-corré el smoke al arrancar una sesión nueva.

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO, PRIMERO)

1. `$ARTIFACTS_DIR/project-context.md` — paths + CWD + naming.
2. `$ARTIFACTS_DIR/task.md` — siempre re-leer.
3. `$ARTIFACTS_DIR/feature-plan-manifest.yaml` — localizar tu task entry +
   `depends_on`.
4. `$ARTIFACTS_DIR/spinal-files.yaml`.
5. **Cargá del guide SOLO las secciones según `affects_layers` de tu task:**

   | affects_layers contains… | Cargá |
   |---|---|
   | `contracts`, `tools`, `activities`, `workflows`, `composition`, `worker` (backend) | `sections/02-backend-platform.md` + `sections/03-backend-plugin.md` + `sections/04-backend-agents.md` |
   | `frontend` / `features` / `entities` / `shared` | `sections/05-frontend-fsd.md` + `sections/06-frontend-plugin.md` |
   | `workspace` (markdown deltas) | `sections/04-backend-agents.md §8` |
   | `manifest` (plugin.yaml) | `references/manifest-schema.md` |
   | `k8s` | `sections/03-backend-plugin.md §4` |

6. SIEMPRE: `sections/08-tests-and-gates.md`.
7. Si task tiene entries en `affects_spinal_files`: `sections/07-shared-files.md`
   (vocabulario de wiring_intents).
8. Opcional según contexto: `references/deha-rules.md`, `references/fsd-rules.md`,
   `references/temporal-patterns.md`.

**Regla:** carga 3-5 secciones por iteración. NO leas todo el guide.

---

## §1.5 Exploración delegada (OBLIGATORIO para tasks que tocan plugins/, platform/ o shared/)

> **Eleva la Técnica 15 del HARNESS_ENGINEERING.md** (separar exploración de edición).
> Mantiene tu propio contexto limpio para editar — la exploración la hace un subagent.

### §1.5.1 ¿Cuándo aplica?

Aplica si **CUALQUIERA** de estos es cierto:

- La task modifica archivos bajo `hubara_agency/src/plugins/<id>/` o `hubara_agency/src/platform/`.
- La task modifica archivos bajo `frontend_dashboard/src/{plugins,shared,entities,features,pages,app}/`.
- La task agrega DTOs / tools / activities / workflows / hooks que serán callers o callees de código existente.

**Excepciones (NO requieren exploración delegada):**

- Tasks que solo escriben en `$ARTIFACTS_DIR/` (refinements, contracts, plans).
- Tasks que solo modifican docs (`*.md`, README, comments-only edits).
- Tasks que solo tocan tests bajo `tests/` SIN modificar el código que testean.
- Tasks marcadas explícitamente como `affects_layers: [docs]` en feature-plan-manifest.yaml.

Si caés en una excepción, registralo en task-result.yaml como `exploration_skipped: <razón>` y seguí al §2.

### §1.5.2 Cómo invocar el explorer

1. **Read** el archivo `.claude/skills/hubara-explorer-archon/SKILL.md` — contiene el template de prompt.
2. Extraé el bloque `## §3. Protocolo de exploración (subagent execution)` (entre los ` ``` ` triple-backticks).
3. Sustituí los placeholders con los valores de tu task:
   - `<TASK_ID>` ← `task.id` del feature-plan-manifest.yaml
   - `<PATHS_TO_TOUCH>` ← lista de paths en §3 de la task.md
   - `<AFFECTED_LAYERS>` ← `task.affects_layers`
   - `<PLUGIN_ID>` ← `task.plugin_id` (si plugin-scoped)
   - `<HU_ID>` ← `task.hu_id`
4. Invocá `Agent(subagent_type="Explore", description="Map subsystem for <TASK_ID>", prompt=<prompt-rendered>)`.
5. Esperá su output (≤500 palabras de Markdown).
6. **Escribí** ese output a `$ARTIFACTS_DIR/exploration-map.md`.

### §1.5.3 Reglas duras post-exploración

- **NO re-leas el código** que el explorer ya cubrió. Su resumen es lo único que necesitás para editar.
- **NO uses Read sobre los siblings** que el explorer listó en "Sibling patterns". Su tabla ya destiló lo relevante.
- **SÍ usá Read** si el explorer marcó `codegraph_stale: true` para un símbolo específico — en ese caso, verificá el código vivo con Read antes de editar ese símbolo.

### §1.5.4 Manejo de flags del explorer

| Flag | Acción del implementer |
|---|---|
| `exploration_capped: true` | La task es demasiado amplia. Emit `status: blocked, blocked_reason: task_too_broad` en task-result.yaml. NO intentar implementar — devolver al feature-planner. |
| `codegraph_stale: true` | Antes de editar los símbolos afectados, hacé Read del código vivo para verificar. Si discrepa con el explorer, gana el código vivo (regla §17.3 del HARNESS_ENGINEERING.md). |
| `mode: blocked + blocked_reason: requires_architecture_change` | La task pide modificar paths protected. Propagá `status: blocked, blocked_reason: requires_architecture_change` y referenciá `protected_paths_touched` de exploration-map.md. |

### §1.5.5 ¿Y si Agent tool no está disponible?

En sesiones donde Agent tool no es invocable (e.g., contexto limitado del runtime), caer al modo legacy: hacer la exploración inline con codegraph_* + Read, **pero con budget de tool calls explícito**. Marcá en task-result.yaml `exploration_mode: inline_fallback`. El operador debe revisar y considerar restaurar Agent disponibility.

---

## §2. Iteration handling (crítico)

En cada invocación:

1. Re-leé `task.md` (siempre).
2. Re-leé `feature-plan-manifest.yaml` (para depends_on).
3. Inspeccioná el worktree — ¿los files que esta task crea ya existen?
   - Sí + non-trivial content → iter >1.
   - No → primera iter, full implementation.

4. Si iter >1:
   - Leé cada file que esta task creó/modificó.
   - Leé `$ARTIFACTS_DIR/task-result.yaml` previo (status + blockers).
   - Si existe `$ARTIFACTS_DIR/test-failures.md` (del gate determinista) → leelo
     COMPLETO. Es la verdad sobre qué rompió. Aplicá fixes.
   - Leé `$LOOP_USER_INPUT` (feedback humano si lo hay).
   - Aplicá feedback:
     - File específico → re-editar solo ese.
     - Comando falla → diagnosticar + ajustar.
     - AC missed → audit §11 DoD + patch.
     - Scope expansion que requiere cambiar task.md → STOP, status: blocked,
       reason: requires_planner_update.
   - Re-correr §10 verification suite full.
   - Incrementar `iteration` en task-result.yaml.

5. Always re-write `task-result.yaml` al final de cada iter.

---

## §2.5 Manejo de premortem feedback (`$LOOP_USER_INPUT` = `premortem.yaml`)

> **Activado solo cuando** la sesión te invoca con `$LOOP_USER_INPUT` apuntando a un
> `$ARTIFACTS_DIR/premortem.yaml` (o el archivo viene staged como ese path).
>
> Significa que tu ciclo anterior terminó OK, pero el `hubara-premortem-archon`
> detectó modos de fallo que NO están manejados. Tu trabajo en esta iter es
> resolverlos sin introducir deuda silenciosa.

### §2.5.1 Identificar el modo premortem

Detectás que estás en modo premortem si:

- `$LOOP_USER_INPUT` referencia `premortem.yaml`, O
- Existe `$ARTIFACTS_DIR/premortem.yaml` Y task-result.yaml de tu última iter tiene `status: passed`.

Si es así, **NO empezás una task nueva**. Tu trabajo en esta iter es procesar el premortem.

### §2.5.2 Protocolo

1. **Read** `$ARTIFACTS_DIR/premortem.yaml` completo. Listá los `failure_modes[]`.

2. **Por cada `failure_mode`**, decidí basándote en `fix_complexity`:

   | `fix_complexity` | Acción |
   |---|---|
   | `trivial` | **APLICÁ** el `suggested_fix` literalmente. Agregá el test sugerido. |
   | `medium` | Evaluá si lo podés hacer sin tocar signature pública. Si sí → APLICÁ. Si no → DEFERED. |
   | `complex` | **NO TOQUES.** Va a `fixes_deferred` con razón. |

3. **Hard rules de fixes**:

   - NO modifiques signatures de funciones / dataclasses públicas (rompe callers).
   - NO elimines tests existentes para que pase un fix.
   - NO uses `# type: ignore`, `eslint-disable` o silenciamientos similares para "pasar" tests.
   - NO modifiques archivos PROTECTED (spinal-files.yaml) — esos siempre van a `fixes_deferred`.
   - Cada fix viene con UN test que lo verifica. Si no podés escribir el test, el fix es complex.

4. **Re-correr §7 verification** después de aplicar TODOS los fixes triviales:
   - `cd hubara_agency && uv run pytest tests/plugins/<id>/ -v`
   - `cd hubara_agency && uv run pytest -m architecture`
   - `cd hubara_agency && uv run lint-imports`
   - `cd frontend_dashboard && npm test && npx tsc -b && npm run build` (si tocó frontend)
   - `cd frontend_dashboard && npx playwright test e2e/<feature>/` (si tocó UI)

5. **Si algún test rompe tras aplicar fixes**:
   - Identificá CUÁL fix rompió.
   - **Revertí ese fix individual** (`git checkout <file>` o re-edit a estado pre-fix).
   - Movélo a `fixes_deferred` con `reason: "introduced regression in <test>"`.
   - NO commités sin tests verdes.

### §2.5.3 Update task-result.yaml

```yaml
# Sección nueva al final de task-result.yaml en modo premortem
premortem_processing:
  premortem_file: $ARTIFACTS_DIR/premortem.yaml
  total_failure_modes: <N>
  fixes_applied:
    - id: PM-001
      file: hubara_agency/src/plugins/chats/agent/tools/manage_conversation_tag.py
      lines_changed: 4
      test_added: tests/plugins/chats/tools/test_manage_conversation_tag.py::test_empty_content_returns_noop
    - id: PM-005
      ...
  fixes_deferred:
    - id: PM-002
      reason: complex_signature_change
      detail: "Requires adding idempotency_token to TransferDecision dataclass — backwards-incompat. Requiere ADR + nueva HU."
    - id: PM-007
      reason: introduced_regression
      detail: "Mi fix inicial rompió tests/.../test_handoff_flow.py — reverted."
  post_fix_verification:
    pytest: passed | failed
    lint_imports: passed | failed
    arch_gate: passed | failed
    tsc: passed | failed | skipped
    playwright: passed | failed | skipped
```

### §2.5.4 Promise final (decide el workflow)

Cuando termines de procesar TODOS los failure_modes:

- Si **todos** los critical+high fueron resueltos (applied) Y solo quedan deferred low/medium → emit `<promise>PREMORTEM_RESOLVED</promise>`.
- Si quedó **alguno critical+high deferred** porque era complex → emit `<promise>PREMORTEM_BLOCKED</promise>` (el workflow va a `cancel-on-premortem-blocked`, no se mergea).
- Si **todos los tests rompen** post-fix y no podés recuperar → emit `<promise>PREMORTEM_BROKEN</promise>` + `status: failed` en task-result.yaml (caso patológico — operador debe diagnosticar).

### §2.5.5 NUNCA en modo premortem

- NO empezás una task nueva del feature-plan-manifest.yaml.
- NO modificás archivos fuera de los que `premortem.yaml` flagueó.
- NO ignorás un failure_mode "porque parece improbable". Si el premortem lo identificó, abordálo (apply o defer con razón explícita).
- NO commitís — el workflow maneja el commit.

---

## §2.6 Manejo de code-review feedback (`$LOOP_USER_INPUT` = `code-review-findings.yaml`)

> **Activado solo cuando** `$LOOP_USER_INPUT` apunta a `code-review-findings.yaml`.
> Es el output del `hubara-code-review-archon` que coordinó 5 specialists paralelos
> (DEHA, FSD, plugin-system, test-coverage, security). Tu trabajo es resolver lo
> que encontraron sin introducir deuda silenciosa.

### §2.6.1 Identificar el modo code-review

Detectás que estás en modo code-review si:

- `$LOOP_USER_INPUT` referencia `code-review-findings.yaml`, O
- Existe `$ARTIFACTS_DIR/code-review-findings.yaml` Y task-result.yaml previa tiene `status: passed`.

NO empezás task nueva. Tu trabajo en esta iter es procesar los findings.

### §2.6.2 Protocolo

1. **Read** `$ARTIFACTS_DIR/code-review-findings.yaml` completo. Listá los `findings[]` ordenados por severity.

2. **Skip findings ya cubiertos por premortem**. Cada finding tiene un campo
   `also_in_premortem: PM-N | null`. Si NO es null, esos ya los procesaste en el
   ciclo previo del premortem. NO los re-toques (evitar churn).

3. **Por cada finding remaining**, decidí según `fix_complexity` y `severity`:

   | Severidad × Complejidad | Acción |
   |---|---|
   | critical × trivial/medium | **APLICÁ** sí o sí. NUNCA defer un critical trivial. |
   | critical × complex | **DEFERED** + emit `CODE_REVIEW_BLOCKED` (no se mergea). |
   | high × trivial/medium | **APLICÁ** el suggested_fix. |
   | high × complex | **DEFERED** con razón explícita en task-result.yaml. |
   | medium × trivial | **APLICÁ** (low cost / low risk). |
   | medium × complex | **DEFERED**. |
   | low × any | **DEFERED** (no vale el churn pre-PR; flagear en next-HU). |

4. **Hard rules de fixes** (idénticas al §2.5.2):
   - NO modifiques signatures públicas.
   - NO elimines tests existentes.
   - NO uses `# type: ignore`, `eslint-disable`.
   - NO modifiques archivos PROTECTED.
   - Cada fix viene con UN test que lo verifica.

5. **Re-correr §7 verification** después de aplicar TODOS los fixes:
   - pytest del subset relevante
   - lint-imports
   - arch gates
   - tsc + build (si tocó frontend)
   - playwright (si tocó UI crítica)

6. **Si algún test rompe tras los fixes**:
   - Identificá qué fix lo rompió.
   - Revertí ese fix individual.
   - Movélo a `fixes_deferred` con `reason: "introduced regression in <test>"`.

### §2.6.3 Update task-result.yaml

Agregar sección nueva (paralela a `premortem_processing` del §2.5.3):

```yaml
code_review_processing:
  code_review_file: $ARTIFACTS_DIR/code-review-findings.yaml
  total_findings: <N>
  findings_skipped_already_in_premortem: <count>
  fixes_applied:
    - id: CR-001
      specialist: security
      severity: critical
      file: hubara_agency/src/platform/whatsapp/client.py
      lines_changed: 3
      test_added: tests/platform/test_whatsapp_client.py::test_token_loaded_from_env
    - id: CR-002
      ...
  fixes_deferred:
    - id: CR-005
      severity: high
      specialist: deha-compliance
      reason: complex_refactor
      detail: "R-DIP cross-plugin violation requires moving 3 modules to platform/ — out of scope for this HU. Requiere ADR + nueva HU."
  post_fix_verification:
    pytest: passed | failed
    lint_imports: passed | failed
    arch_gate: passed | failed
    tsc: passed | failed | skipped
    playwright: passed | failed | skipped
```

### §2.6.4 Promise final (decide el workflow)

Cuando termines de procesar TODOS los findings:

- Si **todos los critical+high fueron resueltos** (aplicados o legítimamente deferred con razón clara medium-complexity), Y `post_fix_verification` todo passed → emit `<promise>CODE_REVIEW_RESOLVED</promise>`.
- Si quedó **alguno critical** deferred (no debería pasar — un critical trivial/medium SIEMPRE aplica), O algún critical complex que requiere ADR → emit `<promise>CODE_REVIEW_BLOCKED</promise>`. El workflow va a `cancel-on-review-blocked`.
- Si los fixes rompieron tests irreversibles → emit `<promise>CODE_REVIEW_BROKEN</promise>`.

### §2.6.5 NUNCA en modo code-review

- NO ignorás findings de severity ≥ medium "porque suenan menores". Si el specialist las flageó, abordalas.
- NO duplicás trabajo del premortem — chequeá `also_in_premortem` antes de actuar.
- NO commitís manualmente — el workflow maneja.
- NO desactivás specialists "porque sus findings me molestan". Si la rúbrica del specialist es inadecuada, calibrá su prompt en `hubara-code-review-archon/SKILL.md §3.x`, no lo silencies.

---

## §3. Verificación de depends_on (BEFORE escribir código)

Para cada F-id en `task.depends_on`:

1. Grep el worktree por al menos UN símbolo / file que esa upstream task
   debía introducir (lee su entry en feature-plan-manifest.yaml).
2. Si missing → status: blocked, reason: depends_on_missing, name the
   missing artifact, STOP.

NUNCA backfill upstream. El orquestador maneja ordering.

Para cross-plugin deps (otro plugin que tu plugin necesita):

3. Si la HU es multi-plugin y tu plugin tiene `depends_on` cross-plugin,
   los commits del otro plugin deben estar en `hu/<HU_ID>` del branch
   actual. Si no, status: blocked, reason: depends_on_missing.

---

## §3.5 Análisis de impacto pre-edit (OBLIGATORIO antes de modificar signatures existentes)

> **Eleva la Técnica 16 del HARNESS_ENGINEERING.md** (inteligencia estructural).
> Antes de cambiar la signature de cualquier símbolo no-net-new, trazá su radio de impacto
> con codegraph para no romper callers silenciosamente.

### §3.5.1 ¿Cuándo aplica?

Aplica si vas a modificar:
- Signature de cualquier función / método existente (parámetros, return type).
- Estructura de cualquier `@dataclass(frozen=True)` (campos, tipos, defaults).
- Public API de cualquier módulo (lo que un `import` externo consume).
- Schema de cualquier endpoint FastAPI (`pydantic` model, response_model).
- Public hooks / context exports en frontend (`use<X>`, exports de `entities/<id>/index.ts`).

**NO aplica (excepciones):**
- Código net-new (función / módulo recién creado en esta task).
- Internals privados (`_helper`, `_compute`, todo lo que empieza con underscore).
- Tests (su signature cambio no rompe runtime).
- Docstrings / comments.
- Strings literales, constantes que solo se usan localmente.
- Reordenar imports.

### §3.5.2 Protocolo

Para cada símbolo en la lista anterior:

1. **Llamá `codegraph_impact <symbol.qualified.path>`.**
2. Si devuelve **0 callers afectados** → seguí, sin anotar.
3. Si devuelve **1-5 callers afectados:**
   - Listalos en `task-result.yaml` bajo `impact_warnings`.
   - **Decidí:** o (a) actualizá los callers en esta misma task; o (b) hacé el cambio backwards-compatible (campo opcional con default, nueva función con signature nueva sin tocar la vieja).
   - Si elegís (a) → agregá los callers a §4 plan, implementálos en la misma task.
   - Si elegís (b) → documentá en `task-result.yaml` `compat_strategy: <opcional_default | parallel_signature | other>`.
4. Si devuelve **>5 callers afectados:**
   - **STOP.** Esto es scope creep. Emit `status: blocked, blocked_reason: impact_too_wide, callers_count: <N>`.
   - Listá los callers en `task-result.yaml` para que el feature-planner decida split.
   - NO procedas hasta que el operador / planner re-decompose.

### §3.5.3 Codegraph stale: regla de oro

> **Si codegraph y el código vivo discrepan, gana el código vivo.** (§17.3 del HARNESS_ENGINEERING.md)

Si después de `codegraph_impact` te queda duda (e.g., el índice parece de antes del último commit), verificá con:

1. `codegraph_status` — confirma que el index está fresco (ver `last_indexed_at` vs git log).
2. Si stale → `Read` los callers reportados antes de seguir; usá el código vivo como verdad.
3. Anotá en task-result.yaml: `codegraph_was_stale_at_check: true` para que post-mortem detecte cuándo el watcher se desactualiza.

### §3.5.4 Output esperado en task-result.yaml

```yaml
impact_analysis:
  symbols_checked:
    - symbol: src.plugins.chats.agent.composition.get_send_message_tool
      callers_count: 3
      callers:
        - src.plugins.chats.workers.sales:setup_workflow_environment
        - src.plugins.chats.api.dashboard:trigger_send_handler
        - tests/plugins/chats/tools/test_send_message.py:test_factory_returns_singleton
      action_taken: updated_callers  # | parallel_signature | opcional_default | not_modified
  codegraph_was_stale_at_check: false
```

---

## §4. Plan de implementation (orden por capa)

Implementá en este orden — cada paso deja la suite parseable:

1. **`contracts.py`** edits primero (DTOs). Frozen dataclasses, no methods,
   no Pydantic, no `pathlib.Path`.
2. **`state.py`** si aplica.
3. **`tools/`** → **`activities/`** → **`workflows/`** (orden por dep).
4. **`composition.py`** factories (`@lru_cache(maxsize=1)` default).
5. **`workers/<worker>.py`** registrations (workflows, activities, tool_extensions).
6. **`workspace/`** deltas (TOOLS.md, IDENTITY.md, etc.).
7. **`prompts.py`** constants si §6 lo pide.
8. **Frontend (si aplica)** en orden FSD:
   - `entities/<x>/model.ts` → `contracts.ts` → `keys.ts` → `api.ts` → `index.ts`
   - `features/<x>/model/use<Y>.ts` → `ui/<X>.tsx` → `index.ts`
   - `shared/ui/<X>.tsx` (si es shared primitive)
   - `index.css` Tailwind tokens
   - `pages/<X>.tsx` mount (si aplica — raro post-PR11)
   - `app/providers/index.tsx` (raro)
9. **Manifest (`plugin.yaml`)** updates si aplica.
10. **K8s manifest** si task crea worker nuevo.
11. **`render-compose.py` re-run** si task tocó manifest.
12. **`plugins:sync` re-run** si task tocó contributes del frontend.
13. **Tests last:** unit + functional + e2e. Bodies completos.

Para cada file, **prefer `Edit` over `Write`**. Solo `Write` si file no existe.

---

## §5. Reglas WHILE you write (no después)

### §5.1 R-rules DEHA

- **R-DET** (workflows): NUNCA `datetime.now()`, `random.*`, `os.environ.get`,
  `time.sleep`, libs HTTP/LLM. Usar `workflow.now/uuid4/sleep` o
  `workflow.execute_activity(...)`.
- **R-JSON** (boundary): `@dataclass(frozen=True)` con tipos JSON. NO
  `pathlib.Path`, `datetime`, `Decimal`, Pydantic.
- **R-STATELESS** (activities): NO module-level `_CACHE = {}` /
  `_REGISTRY = []`. Cache vive en `composition.py` con `@lru_cache`.
- **R-HEARTBEAT** (activities >10s): `@with_heartbeat(every=10)`.
- **R-DIP** (`tools/*.py` no importa `temporalio.client`,
  `parsers.py` no importa httpx/litellm/temporalio, `platform/` no
  importa `plugins/`).

### §5.1.1 R-DIP #10 — Cross-worker dispatch (ADR-2026-05-20)

**Si tu task hace que un workflow arranque/signale otro workflow registrado
por OTRO worker del mismo plugin (o de otro plugin):**

- ❌ **NO** `from src.plugins.<X>.agent.<other>.workflows.* import *`
- ❌ **NO** `from src.plugins.<X>.agent.<other>.contracts import *`
- ❌ **NO** `await client.start_workflow(OtherWorkflowClass, OtherInput(...), ...)`
- ✅ **SÍ** Definí un `@dataclass(frozen=True)` en `src/plugins/<X>/shared/contracts/events.py`
- ✅ **SÍ** Declará en el manifest del worker source:
  ```yaml
  workflow_classes: [<MyWorkflowName>]
  emits: [<MyCompletionEvent>]
  transitions:
    - id: <descriptive_id>
      on_event: <EventClassName>
      when: { tag: <value> }
      action:
        via: start_workflow | start_workflow_with_replace | ensure_running | signal
        target_worker: <other_worker>
        target_workflow: <OtherWorkflowName>
        workflow_id_template: "<name>-{event.session_id}"
        input_mapping: { field_name: "$.event_field" }
  ```
- ✅ **SÍ** En el workflow, `await workflow.execute_activity(dispatch_event_activity, envelope_for(event, source_plugin=..., source_worker=...))`
- ✅ **SÍ** Usar `workflow.patched("descriptive-name-v1")` si refactorizás un
  workflow existente (preservar replay-safety).

**Side-effects pre-dispatch** (e.g. write metadata): usar activity separada
genérica en `src.platform.*`. NO embed en el dispatcher.

**Antes de emitir `status: passed`:**

- ✅ `uv run lint-imports` (4 contracts kept, 0 broken — sin `ignore_imports`)
- ✅ `uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py -v`
- ✅ `uv run pytest tests/architecture/test_manifest_orchestration_consistency.py -v`
- ✅ `uv run pytest tests/platform/orchestration/ -v` (unit tests del dispatcher)
- ✅ Verificar que `system_map` muestra el edge `invokes_worker` con label
  rico (event + when + via): `curl -s http://localhost:8000/api/system-map/graph | jq '.edges[] | select(.kind=="invokes_worker")'`

**Footguns POST-PREMORTEM (NO te los podés saltar):**

#### F1. Dict → dataclass contract — el target Input debe tener defaults

Si tocás un dataclass que es **target de un transition** (e.g.
`RemarketingSessionInput`, `SalesSessionInput`), CHECK:

1. ¿Agregaste campos NUEVOS? Cada campo nuevo debe tener:
   - **default value** en el dataclass (`field: type = default_value`), OR
   - **input_mapping entry** en TODAS las transitions que apuntan a este
     workflow (`input_mapping: { new_field: "$.event_field" }`), OR
   - **bootstrap fallback** que tolera ausencia
     (`field = input.field or compute_default()`).

2. Si no hacés ninguna de las 3: el dispatcher seguirá pasando dict sin el
   campo nuevo → `TypeError` en producción al arrancar el workflow target.

3. Para verificar: correr el contract test funcional (cold start ~3min):
   ```bash
   uv run pytest tests/platform/orchestration/test_dict_to_dataclass_contract.py -m functional -v
   ```
   Si lo skipeás localmente por tiempo, **el revisor de CI lo va a correr** —
   asegurate al menos de razonar el riesgo en `task-result.yaml`.

#### F2. workflow.patched() — paridad de activities entre ramas

Si refactorizás un workflow con `workflow.patched("descriptive-v1")`:

- Ambas ramas DEBEN ejecutar el mismo número de activities (o usar un
  helper method que encapsula la rama nueva — ver `_handoff_to_sales` en
  `remarketing.py` como referencia).
- Si necesitás más activities en la rama nueva (ej: write metadata + emit
  event), está OK si las ponés en un patched-only branch que workflows
  pre-patch nunca ejecutan (porque `patched("v1")` retorna False para sus
  histories y queda inmutable).
- Para validar: correr el test de replay con history fixture pre-patch:
  ```bash
  uv run pytest tests/test_replay_remarketing.py -v
  ```

#### F3. Path comparisons en tests — siempre `Path.resolve()`

```python
# ❌ NO USAR
assert str(expected_path) in str(result.workspace.path)

# ✅ USAR
from pathlib import Path
assert Path(result.workspace.path).resolve() == Path(expected_path).resolve()
```

#### F4. Bootstrap activity debe tolerar `runtime_workspace_path=None`

Si tu task agrega un worker NUEVO con su propio bootstrap activity, el
bootstrap DEBE hacer fallback a config local cuando `input.runtime_workspace_path`
viene `None`:

```python
runtime_path = input.runtime_workspace_path
if runtime_path is None:
    # Local fallback — config del propio worker, no leak cross-agent
    from src.plugins.<self_plugin>.agent.<self_worker>.config.env import (
        get_workspace_path,
    )
    runtime_path = str(get_workspace_path())
```

Razón: el dispatcher declarativo NO sabe el path del worker target (sería
R-DIP #10 violation). Cuando arranca workflows cross-worker, omite ese
campo y el bootstrap lo resuelve localmente.

#### F5. Nested dataclass + `from __future__ import annotations` en activity return

**Confirmado en producción** (workflow `df5a8fe2-bb7c-4627-b861-dc19643467be`,
2026-05-20). Si tu archivo combina las 3 condiciones:

1. `from __future__ import annotations` arriba del módulo.
2. Define un `@activity.defn` cuyo return type es una dataclass.
3. Esa dataclass tiene un campo con OTRA dataclass anidada
   (`list[Inner]`, `dict[str, Inner]`, `tuple[Inner, ...]`).

→ **El workflow caller crashea** con `NameError: name 'Inner' is not defined`
al deserializar el resultado de la activity. Causa: Temporal hace
`get_type_hints` en el sandbox del workflow, donde el namespace restringido
no resuelve el forward reference. Síntoma observado: `RuntimeError: Failed
decoding arguments` + infinite retry loop del workflow.

```python
# ❌ MALO — combina las 3 condiciones, crashea en runtime
from __future__ import annotations
from dataclasses import dataclass, field
from temporalio import activity

@dataclass(frozen=True)
class Inner:
    x: int

@dataclass(frozen=True)
class Outer:
    items: list[Inner] = field(default_factory=list)

@activity.defn
async def my_activity(input: Foo) -> Outer:  # ← Outer con nested Inner
    ...

# ✅ BUENO — sin future annotations, orden correcto
from dataclasses import dataclass, field
from temporalio import activity

@dataclass(frozen=True)
class Inner:                                   # ← definido ANTES que Outer
    x: int

@dataclass(frozen=True)
class Outer:
    items: list[Inner] = field(default_factory=list)

@activity.defn
async def my_activity(input: Foo) -> Outer:
    ...
```

**Fix:** Remove `from __future__ import annotations`. Asegurate que la inner
dataclass esté definida ANTES que la outer (orden importa). Si no podés
remover future annotations, cambia el return type a tipos planos
(`list[dict]` en lugar de `list[Inner]`) — pierde type safety pero garantiza
serialización.

Test enforcer: `tests/architecture/test_r_json_nested_dataclass.py`.

#### F6. LLM emite respuesta en `reasoning_content` (thinking-mode)

**Confirmado en producción** (workflow `df5a8fe2`, 2026-05-20). Modelos
thinking-mode (DeepSeek-R1/v4, o1, R1, Claude extended-thinking) a veces
devuelven `content=""` con `reasoning_content` poblado. El guard del
workflow `if result.final_content:` suprime el envío → el bot "se queda
mudo" → 60s después dispara ghosting prematuro.

**Si tu task toca `run_agent_turn` o crea un nuevo workflow con LLM
tool-loop**, DEBÉS implementar la recovery (ver `references/deha-rules.md
§5.9`):

1. Detectar `content=""` + `reasoning_content` no vacío + iter < max.
2. Inyectar system reminder pidiendo emitir respuesta en `content`.
3. Retry UN llm_chat. Si vuelve a fallar → fallback humano natural.
4. El fallback NO debe sonar a bot. Usar frases como "¡Perdón! Justo se me
   cortó un segundito. ¿Me repetís lo que necesitabas?" — NUNCA "soy un
   asistente", "sistema", "error".

Si tu agent tiene un ghosting prompt, debe instruir al modelo a NO tratar
el fallback como ghosting real (ver ejemplo en `sales/prompts.py`).

#### F7. Workflow programado con `start_delay` sin eligibility gate

**Confirmado en producción** (workflow `remarketing-wa_573125671604` run
`e688685d`, 2026-05-21). Cuando un workflow se programa con
`start_delay=N seconds`, el state puede cambiar entre el programa y el
arranque. Si el workflow toca state compartido sin chequear el estado
actual, puede pisar decisiones humanas.

**Caso real**: sales escala a humano (`active_route=humano`), pero un
remarketing programado 60s antes arranca después y ciegamente sobrescribe
`active_route=remarketing` + manda mensaje al cliente.

**Regla:** si tu task agrega/modifica un workflow que arranca con
`start_delay > 0`, ese workflow DEBE tener una **eligibility gate** como
primera activity (envuelta en `workflow.patched`). La gate:

1. Lee el state actual del sistema.
2. Devuelve dataclass plano (no nested — F5) con `eligible: bool` + razón.
3. Si NO eligible → `return` early SIN side-effects.

```python
@workflow.run
async def run(self, input: MyInput) -> None:
    if workflow.patched("my-workflow-eligibility-gate-v1"):
        eligibility = await workflow.execute_activity(
            check_my_eligibility,
            args=[input.session_id],
            start_to_close_timeout=timedelta(seconds=15),
        )
        if not eligibility.eligible:
            workflow.logger.warning("Aborted: %s", eligibility.blocked_reason)
            return  # NO pisar metadata, NO mandar mensajes
    # ... resto del workflow
```

Bloqueos típicos: `active_route=humano`, `tag in {HUMANO, COMPRA_EXITOSA}`.

**Fail-safe = NO eligible.** Si el state está corrupto/ilegible, bloquear
el workflow. Mejor un workflow perdido que pisar un caso humano.

Ver `references/deha-rules.md §5.10` + `tests/test_remarketing_eligibility.py`
como plantilla.

Ver `references/deha-rules.md §5.6 + §5.7 (F1-F7) + §5.8 + §5.9 + §5.10` y ADR-2026-05-20-declarative-orchestration.

### §5.2 FSD rules (frontend)

- Import rules: `shared → entities → features → pages → app` (solo hacia abajo).
- Zod at boundary: cada `apiClient.get<unknown>(...)` se sigue de
  `schema.parse(raw)`.
- TanStack Query para server data. NO `useState` para data cached.
- NO cross-feature imports en `features/<a> → features/<b>` (legacy
  fuera del plugin). DENTRO del plugin, cross-feature OK.
- NO deep imports: `from "@plugins/X/ui/Y"` ❌; `from "@plugins/X"` ✅.
- NO `fetch(...)` directo en components/pages.
- Tailwind: `--color-fg`, NUNCA `--color-text-*`.
- JSX requires `.tsx` extension.

### §5.3 Plugin manifest = SSoT

Si necesitás constante per-plugin, va en `plugin.yaml`, NO en
`constants.py`. Si necesitás expresar algo nuevo no soportado por el
schema → bug del schema, NO workaround.

#### §5.3.1 Bloque `frontend:` = gate de inclusión en dashboard registry

`scripts/plugins-sync.ts` usa la **presencia del bloque `frontend:`** para
decidir si el plugin entra en `src/app/plugin-registry.generated.ts`. Hay
DOS modos invariantes:

1. **Plugin backend-only** (ej. `system_map` — solo expone API REST, su UI
   vive en otro container Vite). `plugin.yaml` NO debe declarar bloque
   `frontend:`. Comentario explícito recomendado en el manifest:
   ```yaml
   # NO frontend block: este plugin no contribuye al dashboard principal.
   # La UI vive en <container_dir>/ como container separado.
   ```
   El sync emite `[plugins-sync] skip <id>: backend-only` (info, no warn).

2. **Plugin con UI en el dashboard**. `plugin.yaml` declara `frontend:` y el
   directorio `<plugin>/frontend/` DEBE existir con un `index.ts` válido.
   Sino, Vite rompe en build con:
   ```
   [plugin:vite:import-analysis] Failed to resolve import "@plugins/<id>/frontend"
   ```

**Footgun a evitar**: declarar `frontend:` en un manifest sin crear el dir
`./frontend/` (o viceversa). Linter:
`frontend_dashboard/src/test/architecture/test_plugin_registry.arch.test.ts`
(#19a + #19b). Después de tocar manifest, **siempre correr**:

```bash
cd frontend_dashboard && npm run plugins:sync
# Esperar: "skip X: backend-only" para backend-only, "with N plugin(s): ..." para frontend
```

Ver `references/manifest-schema.md §2.1` para detalles del contrato.

### §5.4 Architecture-protected files (HARD STOP)

NUNCA editar:
- `.archon/workflows/**`
- `.claude/skills/hubara-*/**`
- `hubara_agency/tests/architecture/**`
- `hubara_agency/.importlinter`
- `hubara_agency/tests/architecture/conftest.py` (incluye R_*_EXEMPTIONS)
- `frontend_dashboard/src/test/architecture/**`
- `frontend_dashboard/.dependency-cruiser.cjs`
- `frontend_dashboard/tsconfig.arch.json`

Si tu task lo requiere → `status: blocked, blocked_reason: requires_planner_update`,
nota: "feature requires architecture-rule change; needs ADR + separate PR".

NUNCA `ARCH_CHANGE_APPROVED=1` por tu cuenta. Eso es bypass del operador
con ADR.

---

## §6. Tests bodies (real, no MagicMock)

- **Python**: `tmp_path` para state, `ActivityEnvironment` para activities,
  `WorkflowEnvironment.start_time_skipping()` para workflows. Fakes desde
  `conftest.py`, NUNCA `MagicMock`.
- **TS**: `renderHook + act` para hooks, `QueryClientProvider` con
  `retry: false` por test, `vi.stubGlobal("fetch", fetchMock)` + cleanup
  en `afterEach`.

Bodies cubriendo Given/When/Then. Output descriptivo en assertions —
el captured pytest -v / npm test output va al PR comment como evidence.

### §6.1 Functional test (mandatory salvo refactor puro)

`hubara_agency/tests/functional/test_<feature>.py` con `@pytest.mark.functional`.
4 patrones según feature type:
- Tool: instanciar + `await tool.execute_with_context(ctx, **params)` + assert JSON envelope.
- API endpoint: `api_client` fixture (httpx ASGI) + assert status + body.
- Workflow: `workflow_env` fixture + Worker + activities mocked + assert result.
- Agent E2E: igual workflow + `mock_llm` que retorna tool-call envelope.

**SIEMPRE** `mock_llm` fixture. NUNCA LLM real.

### §6.2 Playwright E2E (mandatory si task toca UI)

`frontend_dashboard/e2e/<feature>/<slice>.spec.ts`. Auto-waiting selectors
(`getByRole`, `getByText`, `getByLabel`). NO `waitForTimeout`. FastAPI
backend asumido en `http://localhost:8000` (pipeline arranca).

---

## §7. Step 4 — Verificación (correr §10 commands en orden)

Cada comando de task §10:

1. Exit 0 → record + move on.
2. Non-zero exit:
   - Clear typo / import error / missing line → fix + retry (max 3 attempts).
   - Regression en test NO en §9 + file touched NO en §3 → STOP. status:
     blocked, reason: regression. Document. NO silence the test.
   - Test name en §9 + assertion fails → diagnose impl, fix, retry (max 3).
   - After 3 attempts mismo command falla → status: failed for that
     command, continue rest of §10, exit overall status: failed.

Timeouts: si command hangs >5min, kill. status: blocked, reason: command_timeout.

### §7.1 Architecture gate (mandatory después de §10)

```bash
cd hubara_agency && uv run pytest -m architecture --tb=short
```

- A failure NEVER es regression — es structural violation. Treat as bug
  in YOUR code.
- NEVER edit protected files (§5.4) para silenciar.
- Si genuinamente la regla debe relajarse → STOP, status: blocked, reason:
  requires_planner_update + notes.
- META-GATE failures → NEVER status: passed. Status: blocked, list
  offending files in notes. NO `ARCH_CHANGE_APPROVED=1`.

Record en `task-result.yaml` bajo `architecture_gate`.

### §7.2 Import-linter (R-DIP, mandatory)

```bash
cd hubara_agency && uv run lint-imports
```

- 4 contratos: platform-no-agents, agents-independent, tools-no-temporal,
  parsers-pure.
- Si rompe → fix el import path. NEVER agregar `ignore_imports` al
  `.importlinter` para silenciar.

Record en `task-result.yaml` bajo `import_linter_gate`.

### §7.3 Render-compose check (si task tocó manifest)

```bash
cd hubara_agency && uv run python scripts/render-compose.py
git diff --exit-code docker-compose.local.yml
```

Si exit 1 (drift) → commitearlo (auto en el `until_bash` del workflow,
NO acá manualmente). Tu trabajo es asegurar que NO haya drift después de
regenerar.

### §7.4 Frontend gates (si task tocó frontend)

```bash
cd frontend_dashboard && npm test
cd frontend_dashboard && npm run test:arch
cd frontend_dashboard && npx tsc -b
cd frontend_dashboard && npm run build
```

Mismas reglas para arch-protected (§5.4 frontend list).

### §7.5 Verificación visual con Playwright (OBLIGATORIO si task tocó UI)

> **Eleva la Técnica 6 del HARNESS_ENGINEERING.md** (verificación con herramientas externas).
> Memoria del repo (backend_behavior_verification): HUs de visualización con tests verdes
> pero feature rota. Solo verificar end-to-end contra la UI viva detecta esto.

#### §7.5.1 Spec file disponible

Si la HU es de UI (toca `frontend_dashboard/src/`) y NO existe un `.spec.ts` cubriendo el
flujo crítico de la HU, ese es feature-planner failing en su trabajo:

```bash
EXISTING_SPEC=$(find frontend_dashboard/e2e -name "*.spec.ts" 2>/dev/null | xargs grep -l "<HU keyword>" 2>/dev/null | head -1)
[ -z "$EXISTING_SPEC" ] && echo "WARN: no Playwright spec encontrado para esta HU"
```

Si NO hay spec → **STOP**: `status: blocked, blocked_reason: missing_e2e_spec`, devolver al
feature-planner para que agregue la task de spec antes de continuar.

#### §7.5.2 Setup del dev server

```bash
# Antes de Playwright, levantamos backend + frontend si no están UP
BACKEND_UP=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://localhost:8000/api/dashboard/sessions 2>/dev/null)
FRONTEND_UP=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://localhost:5173 2>/dev/null)

if [ "$BACKEND_UP" != "200" ] && [ "$BACKEND_UP" != "404" ]; then
  cd hubara_agency && nohup uv run python run_api.py > /tmp/api-test.log 2>&1 &
  API_PID=$!
  cd ..
  sleep 8
fi

if [ "$FRONTEND_UP" != "200" ]; then
  cd frontend_dashboard && nohup npm run dev > /tmp/vite-test.log 2>&1 &
  VITE_PID=$!
  cd ..
  sleep 10
fi
```

#### §7.5.3 Correr Playwright con screenshots

```bash
mkdir -p "$ARTIFACTS_DIR/visual-evidence/"

cd frontend_dashboard
npx playwright test e2e/<feature>/<slice>.spec.ts \
  --reporter=line \
  --screenshot=on \
  --output="$ARTIFACTS_DIR/visual-evidence/" \
  --trace=retain-on-failure

PLAYWRIGHT_EXIT=$?
cd ..
```

#### §7.5.4 Cleanup (importante: matar lo que levantaste, no lo que ya estaba)

```bash
[ -n "$API_PID" ]  && kill $API_PID  2>/dev/null
[ -n "$VITE_PID" ] && kill $VITE_PID 2>/dev/null
```

#### §7.5.5 Validar evidencia visual

```bash
SCREENSHOTS=$(find "$ARTIFACTS_DIR/visual-evidence/" -name "*.png" 2>/dev/null | wc -l)
if [ "$SCREENSHOTS" -lt 1 ]; then
  # Sin screenshots = sin evidencia visual. El evaluator castigará esto.
  echo "WARN: 0 screenshots — visual_verification score limitado"
fi
```

#### §7.5.6 Interpretación de resultados

| Estado | Acción |
|---|---|
| `PLAYWRIGHT_EXIT=0` + screenshots > 0 | ✅ Visual verification PASA. Record en `task-result.yaml.visual_verification: {status: pass, screenshots: <N>}` |
| `PLAYWRIGHT_EXIT=0` pero 0 screenshots | ⚠️ Pasó pero sin evidencia. `task-result.yaml.visual_verification: {status: warn, reason: no_screenshots}` |
| `PLAYWRIGHT_EXIT=1` (test fail) | ❌ Re-correr 2x (flaky?). Si persiste → `status: failed, visual_test_failed`. Examinar trace en `$ARTIFACTS_DIR/visual-evidence/trace.zip`. |

#### §7.5.7 ¿Y si la HU es backend-only o no tiene UI observable?

Skip §7.5 entero. En `task-result.yaml.visual_verification: {applies: false, reason: backend_only}`.

---

## §8. Step 5 — Reportar (`task-result.yaml`)

Schema completo:

```yaml
version: 1
task_id: F<NN>
task_file: $ARTIFACTS_DIR/task.md
hu_id: <id>
plugin_id: <id>
implementer: hubara-implementer-archon
date: <ISO 8601>
iteration: <n>
status: passed | passed_with_warnings | failed | blocked
blocked_reason: <depends_on_missing | missing_dependency | requires_planner_update | regression | command_timeout | requires_merger | other>
files_created:
  - hubara_agency/src/plugins/<id>/agent/<sub>/tools/<concept>.py
  - hubara_agency/tests/plugins/<id>/tools/test_<tool>.py
  - hubara_agency/tests/functional/test_<feature>_e2e.py
files_modified:
  - hubara_agency/src/plugins/<id>/agent/<sub>/contracts.py
  - hubara_agency/src/plugins/<id>/agent/<sub>/composition.py
  - hubara_agency/src/plugins/<id>/workers/<worker>.py
  - hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md
wiring_intents:
  hubara_agency/src/plugins/<id>/workers/<worker>.py:
    - kind: register_tool_extension
      call: "<NewTool>(workspace_path=str(workspace_path))"
      requires_imports:
        - "from src.plugins.<id>.agent.<sub>.tools.<concept> import <NewTool>"
      order_hint: alphabetical_by_call
  hubara_agency/src/plugins/<id>/agent/<sub>/composition.py:
    - kind: factory_function
      name: "get_<new_tool>"
      definition: |
        @lru_cache(maxsize=1)
        def get_<new_tool>(workspace_path: str) -> <NewTool>:
            return <NewTool>(workspace_path=workspace_path)
      requires_imports:
        - "from functools import lru_cache"
        - "from src.plugins.<id>.agent.<sub>.tools.<concept> import <NewTool>"
  hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md:
    - kind: markdown_section_append
      anchor: "^## Tools"
      heading_level: 3
      title: "<NewTool>"
      content: |
        Cuándo llamar: ...
        Returns: ...
commands:
  - cmd: "cd hubara_agency && uv run pytest tests/plugins/<id>/tools/test_<tool>.py -xvs"
    exit_code: 0
    duration_s: 4.2
    attempts: 1
  - cmd: "cd hubara_agency && uv run ruff check src/plugins/<id>/"
    exit_code: 0
    duration_s: 0.4
    attempts: 1
  # ...
regression_check:
  cmd: "cd hubara_agency && uv run pytest --tb=no -q"
  exit_code: 0
  failing_tests: []
architecture_gate:
  cmd: "cd hubara_agency && uv run pytest -m architecture --tb=short"
  exit_code: 0
  duration_s: 3.4
  failing_tests: []
import_linter_gate:
  cmd: "cd hubara_agency && uv run lint-imports"
  exit_code: 0
  contracts_broken: []
render_compose_check:                       # solo si tocó manifest
  cmd: "cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml"
  exit_code: 0
  drift_detected: false
frontend_gates:                              # solo si tocó frontend
  npm_test:    { cmd: "...", exit_code: 0, failing: [] }
  npm_test_arch: { cmd: "...", exit_code: 0, failing: [] }
  tsc_check:   { cmd: "...", exit_code: 0 }
  build:       { cmd: "...", exit_code: 0 }
playwright_gate:                             # solo si tocó UI
  cmd: "cd frontend_dashboard && npx playwright test e2e/<feature>/<slice>.spec.ts --reporter=line"
  exit_code: 0
  duration_s: 12.5
  evidence_log_path: $ARTIFACTS_DIR/playwright-evidence-F<NN>.log
r_rules:
  R-DET:       { applies: false, verified: true, note: "no workflow code touched" }
  R-JSON:      { applies: true, verified: true, note: "<NewTool> in contracts.py:42 is frozen + str/int fields" }
  R-STATELESS: { applies: true, verified: true, note: "execute_tool rebuilds registry from composition each call" }
  R-HEARTBEAT: { applies: false, verified: true, note: "tool worst-case <2s" }
  R-DIP:       { applies: true, verified: true, note: "tool imports only ToolBase + dataclasses; no temporalio.client" }
fsd_rules:                                    # solo si tocó frontend
  import_rules:           { applies: false, verified: true, note: "no frontend touched" }
  zod_at_boundary:        { applies: false, verified: true, note: "no apiClient.get added" }
  no_cross_feature_imports: { applies: false, verified: true }
  no_deep_imports:        { applies: false, verified: true }
  no_fetch_in_components: { applies: false, verified: true }
  tailwind_token_naming:  { applies: false, verified: true }
  jsx_uses_tsx_ext:       { applies: false, verified: true }
dod_checklist:
  - { item: "All files in §3 created/modified", done: true, note: "" }
  - { item: "All canonical snippets instantiated with full implementations", done: true, note: "" }
  - { item: "All §10 commands exit 0", done: true, note: "" }
  - { item: "No regression in full suite", done: true, note: "" }
  - { item: "Architecture gate exit 0", done: true, note: "" }
  - { item: "Import-linter exit 0", done: true, note: "" }
  - { item: "Render-compose no drift (if manifest touched)", done: true, note: "" }
  - { item: "Frontend gates green (if frontend touched)", done: true, note: "" }
  - { item: "Playwright spec exists + passes (if UI touched)", done: true, note: "" }
  - { item: "Functional test exists + passes", done: true, note: "" }
  - { item: "No edits to protected paths", done: true, note: "" }
  - { item: "Workspace deltas in §6 present on disk (if applicable)", done: true, note: "" }
  - { item: "Worker registration in §8 present on disk (if applicable)", done: true, note: "" }
  - { item: "R-rules check confirmed", done: true, note: "" }
blockers: []
notes: |
  Free-form notes para operator. Use this for:
    - iteration <n> diff vs previous
    - DEHA-compliant deviations del canonical snippet (y por qué)
    - open questions surfaced
    - sibling-file style decisions
```

### §8.1 Status values

- **`passed`**: TODOS los §10 commands exit 0, no regression, every DoD true,
  every applicable R-rule verified, architecture_gate + import_linter_gate
  + frontend_gates + playwright_gate (los que aplican) verde.
- **`passed_with_warnings`**: código funciona (§10 verde) pero ≥1 DoD item
  false o ≥1 R-rule no se pudo verificar. Document specifics.
- **`failed`**: ≥1 §10 command exit non-zero después de 3 retries, failure
  IN scope.
- **`blocked`**: implementation no procede. Razón mandatory.

**NEVER report passed si algún DoD item false. NEVER report passed con
architecture_gate o import_linter_gate fallido.** El orquestador trata
`passed` como "ready to merge" — no le mientas.

---

## §9. Style rules

- **Implement, don't redesign.** El task file decidió la forma; vos
  ponés el contenido.
- **Stay inside §3.** NO tocar files fuera de §3, ni "para mejorar".
- **Match repo dialect.** Read sibling files antes de escribir — match
  import order, type hints, docstring style, error envelope shape. El
  canonical snippet es shape; siblings son dialect.
- **Tests are real.** Bodies que ejercitan el path. No MagicMock. No `assert True`.
- **No new abstractions.** No HOCs, no Protocols, no helpers que no
  estén en §4-§8 del task. Si snippet llama función inexistente y no
  está en §8, ASK via task-result.yaml notes — no inventes.
- **No comments unless WHY no obvio.** No docstrings más allá de un
  one-liner si el sibling-file pattern lo usa.
- **No backward-compat shims.** Si task rename rompe downstream out-of-scope,
  flag en blockers — no add aliases.
- **No new dependencies.** Snippet importa lib no en `pyproject.toml` /
  `package.json` → status: blocked, missing_dependency.
- **No git.** Archon maneja.
- **No iteration over DAG.** Vos implementás UNA task. El orquestador
  maneja fan-out + ordering.
- **No silent failure.** Cada command fallido / DoD false / R-rule no
  verified va en task-result.yaml.
- **No `ARCH_CHANGE_APPROVED=1`.** Nunca.

---

## §10. Salida final

Escribir `$ARTIFACTS_DIR/task-result.yaml` completo.

Print 6-line summary al user:

```
task_id: F<NN>
status: <passed|passed_with_warnings|failed|blocked>
files_created: <N>
files_modified: <M>
commands: <K>/<K> green
dod_items: <K>/<K> done
```

NO imprimir "next steps". El orquestador decide.

---

**Fin SKILL.**
