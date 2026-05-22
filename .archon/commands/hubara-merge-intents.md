---
description: Consolida wiring_intents de N implementer agents paralelos (un batch del DAG plugin-level) en los spinal files declarados en hubara_agency/.hubara/spinal-files.yaml. Diseñado exclusivamente para invocación desde el workflow hu-hubara-pipeline (rama multi-plugin, después de merge-fan-out-batch). Lee plugin-*-result.yaml de hubara_agency/.hubara/results/<HU_ID>/ + spinal-files.yaml stageado en $ARTIFACTS_DIR, aplica cada intent deterministically (ordenado por plugin-id, después por kind, después por identifier), escribe spinal files modificados in-place en el worktree, y emite $ARTIFACTS_DIR/merge-report.yaml. NO escribe feature code, NO corre tests, NO commit/push (el orquestador maneja git). Triggers - invocación via Archon workflow skills field; no usar como subagent directo.
argument-hint: (none — reads from $ARTIFACTS_DIR)
---


# hubara-merger-archon — Consolidador de wiring_intents cross-plugin

Sos un merger determinístico. El orquestador `hu-hubara-pipeline`
acaba de correr N sub-pipelines en paralelo (uno por plugin del batch
multi-plugin). Cada uno emitió `wiring_intents` describiendo qué agregar
a spinal files (cross-plugin shared files).

Tu job: aplicar TODOS los intents en orden determinístico al worktree
actual, validar sintaxis, emitir merge-report.

NO escribís feature code. NO corrés tests. NO hacés git. NO iterás
(no `$LOOP_USER_INPUT`).

---

## §0. Invocation contract

- `$ARTIFACTS_DIR/spinal-files.yaml` — la declaración de qué paths son
  spinal y de qué kind. Stageado por el orquestador.
- `$ARTIFACTS_DIR/project-context.md` — paths reales del repo.
- `hubara_agency/.hubara/results/<HU_ID>/plugin-*-result.yaml` — leés
  directo del repo (todos los plugins del batch ya commitearon sus
  results vía git rebase en sub-pipeline FASE 3).
- `$USER_MESSAGE` (opcional) — el orquestador puede pasar el HU_ID +
  batch_id + plugins list para contexto.
- Worktree actual: el worktree del orquestador, con commits de TODOS los
  sub-pipelines del batch ya merged via `git fetch + ff-only`. Spinal
  files en su estado de hu/<HU_ID> (que es main + N sub-pipeline commits).
- Output:
  - Spinal files modificados in-place.
  - `$ARTIFACTS_DIR/merge-report.yaml`.

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO, PRIMERO)

1. `$ARTIFACTS_DIR/project-context.md`.
2. `$ARTIFACTS_DIR/spinal-files.yaml` — para conocer cada path + `kind`.
3. `.claude/skills/hubara-architecture-guide/sections/07-shared-files.md`
   — vocabulario completo de kinds.

NO cargues otras secciones del guide. El merger es lean.

---

## §2. Step 1 — Identificar HU_ID + plugin-results del batch

Del `$USER_MESSAGE` o del worktree:

```bash
# Si USER_MESSAGE = "HU-XXX B1 [chats, catalog]"
# parseá HU_ID + batch_plugins.

# Si USER_MESSAGE vacío, buscá el HU id en el branch actual:
git rev-parse --abbrev-ref HEAD       # → hu/HU-20260517-143025-...
# extraé HU_ID quitando "hu/"
```

Después listar los plugin-results del HU:

```
hubara_agency/.hubara/results/<HU_ID>/plugin-*-result.yaml
```

Filtrar los del batch actual (si batch_plugins se especificó, usar esa
lista; si no, usar TODOS los plugin-results existentes).

---

## §3. Step 2 — Agregar wiring_intents

Para cada plugin-result.yaml:

1. Leer el yaml. Verificar `status: passed` (o `passed_with_warnings`).
   Si `failed` o `blocked`, ABORTAR (orchestrator debió haber filtrado).
2. Iterar sus `feature-results/<plugin>/F*-result.yaml` para extraer
   `wiring_intents` de cada feature task:
   ```bash
   FEATURE_RDIR="hubara_agency/.hubara/results/${HU_ID}/feature-results/${PLUGIN_ID}"
   for f in "$FEATURE_RDIR"/F*-result.yaml; do
     # extraer wiring_intents
   done
   ```

3. Aggregate por spinal_file:
   ```python
   intents_by_file = {
     "<spinal_path>": [(F-id, plugin_id, intent_dict), ...]
   }
   ```

4. Sort cada lista por (plugin_id ASC, F-id ASC, intent order within F).

---

## §4. Step 3 — Validar pre-apply

Para cada spinal_path en `intents_by_file`:

1. Verificar que matchea entry de `spinal-files.yaml` (glob expansion
   soportado: `hubara_agency/src/*/contracts.py` matchea cualquier).
2. Si NO matchea → error: `intent_for_non_spinal_path` + restaurar
   `merge-report.yaml status: failed`, abortar.
3. Verificar `kind` declarado en el intent matchea el `kind` del
   spinal-files entry. Si no matchea → error: `kind_mismatch`.
4. Si path está marcado `protected: true` en spinal-files.yaml → error:
   `protected_file_in_intents`. ABORTAR — eso es bug del feature task
   (debió bloquearse con requires_planner_update).

---

## §5. Step 4 — Apply intents por spinal file

Process files independientemente (failure en uno no rompe otros):

### §5.1 Para cada spinal_path con intents

1. Resolver actual file path (expand glob si aplica).
2. Read current content (estado del worktree, que ya es main + sub-pipeline commits).
3. Si file NO existe → crear empty + intents lo poblan.
4. **Aggregate `requires_imports`** de TODOS los intents para este file:
   - Deduplicate (set semantics).
   - Group: stdlib | third-party | local.
     - stdlib: `import X` o `from X` donde X ∈ stdlib set
       ({abc, asyncio, collections, dataclasses, datetime, functools, json, os, pathlib, re, sys, typing, uuid, ...}).
     - local: empieza con `import src.` o `from src.` o relative
       (`.`/`..`) — o para TS, paths con `@/` o `./`.
     - third-party: el resto.
   - Sort alfabético dentro de cada group.
   - Insert al top del file, respetando:
     - After module docstring si existe.
     - After `from __future__ import ...` si existe.
     - Before first non-import statement.
     - PEP 8 ordering: stdlib → blank → third-party → blank → local → blank → existing.

5. **Apply cada intent en orden** según `kind` (ver §5.2 reglas por kind).
6. **Validate result:**
   - `.py`: `python3 -c "import ast; ast.parse(open('<path>').read())"`.
   - `.ts/.tsx`: parse mínimo (matched braces, valid header).
   - `.md`: headings well-formed.
   - `.yaml`: `python3 -c "import yaml; yaml.safe_load(open('<path>'))"`.
   - `.css`: matched braces.
   - Si parse falla → **restore main-state for that file** + record error.
7. Track stats: `intents_applied`, `intents_skipped`, `new_imports_added`.

### §5.2 Reglas por kind

**`register_tool_extension`** (target: `python_workflow_list` files):
- Locate existing `register_tool_extension(...)` call block.
- Build line: `register_tool_extension("<namespace>", <call_factory>)`.
- Si line exacta ya existe → skip (idempotent).
- Else append al final del block (o crear before `worker.run()` si ausente).

**`workflows_list_item`** (target: `python_workflow_list`):
- Locate `workflows=[` inside `Worker(...)` constructor.
- Sort by `class_name` ASC.
- Si class_name ya en list → skip.
- Else insert before `]` preservando trailing comma.

**`activities_list_item`** — igual que workflows_list_item pero `activities=[...]`.

**`factory_function`** (target: `python_factory_module`):
- Append full `definition` al final del file.
- Sort by `name` ASC entre new additions (no reorder existing).
- One blank line entre factories.
- Same-name collision: same content → skip; diferente → error + restore.

**`dataclass_def`** (target: `python_dataclass_module`):
- Igual factory_function pero para `@dataclass(frozen=True) class X: ...`.

**`constant_def`** (target: `python_factory_module` o `python_constants_module`):
- Append `<name> = <value>` al final.
- Sort by name.
- Same-name collision: same value → skip; diferente → error.

**`python_dict_entries_append`** (target: `python_dict_entries_append` files
e.g. `R_JSON_FROZEN_EXEMPTIONS`):
- ABORTAR — estos files son PROTECTED. Feature task debió bloquearse.
- Record error `protected_dict_modification`.

**`markdown_section_append`** (target: `markdown_section_append` files
e.g. `workspace/TOOLS.md`):
- Locate anchor heading via regex (anchor expects line-start regex like
  `^## Tools`).
- Si anchor found:
  - Determine heading level (count `#`).
  - Find end of anchor's subtree (next line with `<= heading_level` # OR EOF).
  - Append after subtree's last line (before next heading or EOF):
    `\n<#-repeated heading_level> <title>\n\n<content>\n`.
  - Sort multiple intents under same anchor by `title` ASC.
- Si anchor NOT found:
  - Append at EOF con blank line separator.
  - Record warning: `anchor_not_found`.
- Same-title-under-same-anchor → skip (idempotent).

**`ts_barrel`** (target: `entities/*/index.ts`, `features/*/index.ts`,
`shared/*/index.ts`):
- Append `export { X } from "./Y";` o `export type { X } from "./Y";`.
- Sort by export name ASC.
- Same statement already present → skip.

**`ts_factory_module`** (target: `entities/*/api.ts`, `keys.ts`):
- Append función / hook export al final.
- Sort by `name` ASC entre new additions.
- Same name collision: same body → skip; diferente → error.

**`ts_dataclass_module`** (target: `entities/*/model.ts`, `contracts.ts`):
- Append interface / type / Zod schema al final.
- Sort by `name` ASC.

**`ts_object_entries_append`** (target: `shared/ui/Icon.tsx:ICONS`):
- Locate object literal block (usually `export const ICONS = {`).
- Append `<name>: <value>,` antes de `}`.
- Sort by `name` ASC entre new additions.

**`ts_function_body`** (target: `plugins-sync.ts` rara — function specific):
- Modificación localizada. Si dos intents requieren mutaciones distintas →
  error: `ts_function_body_conflict`. ABORTAR.

**`page_feature_mount`** (target: `pages/*.tsx`):
- Append `<NewFeature ... />` dentro de `container_anchor` (usualmente
  un `<div className="col-right glass-panel">`).
- Sort by component name (o usar `order_hint: append` si layout-significant).

**`provider_wrap`** (target: `app/providers/index.tsx`):
- Wrap `<NewProvider>` around `{children}` o around inner provider.
- `order_position: outer` → wraps fuera de todo.
- `order_position: inner` → wraps cerca de `{children}`.

**`tailwind_token`** (target: `src/index.css @theme {}`):
- Append `<name>: <value>;` dentro del `@theme {}` block.
- Sort by `name` ASC.

**`css_theme_block`** — alias de `tailwind_token`.

**`yaml_dict_keys_append`** (target: `plugin.schema.yaml` y similares):
- ABORTAR si la schema YAML es target — schema changes requieren ADR.
- Para otros yaml: append key bajo anchor declarado, validar yaml parse.

---

## §6. Step 5 — Emit `merge-report.yaml`

Schema completo:

```yaml
version: 1
hu_id: <id>
batch_id: <B1 | B2 | ...>
plugins: ['chats', 'catalog']
merger: hubara-merger-archon
date: <ISO 8601>
status: ok | partial | failed
spinal_files:
  - path: hubara_agency/src/platform/contracts.py
    intents_applied: 3
    intents_skipped: 1                    # already present (idempotent)
    intents_errored: 0
    new_imports_added: 2
    validation: passed
  - path: frontend_dashboard/src/shared/ui/Icon.tsx
    intents_applied: 2
    intents_skipped: 0
    intents_errored: 0
    new_imports_added: 1
    validation: passed
  - path: workspace/TOOLS.md                              # ejemplo de partial
    intents_applied: 2
    intents_skipped: 0
    intents_errored: 1
    validation: failed
    error: "anchor '^## Sub-tools' not found; intent appended at EOF (warning, no error)"
errors: []
warnings:
  - "Anchor '^## Sub-tools' not found in workspace/TOOLS.md for F04's intent; appended at EOF"
notes: |
  Free-form. E.g. "F02 and F05 both added factory get_x with identical
  bodies; deduplicated to one."
```

Status values:
- **`ok`**: todos los intents applied (o idempotently skipped); todos los
  validations passed.
- **`partial`**: ≥1 spinal file restored to main-state due to validation
  failure o same-name-different-content collision. **Orchestrator NO debe
  mergear este batch a main sin operator review.**
- **`failed`**: fatal precondition error (missing result file, missing
  spinal entry, protected file modification, etc.). NADA modificado.
  Orchestrator MUST ABORT.

---

## §7. Print summary al user (5 líneas)

```
batch_id: <B1>
spinal_files_touched: <N>
intents_applied: <K>
intents_skipped (idempotent): <S>
intents_errored: <E>
status: <ok | partial | failed>
```

---

## §8. Style rules

- **Spinal files only.** NUNCA toques new files (cada implementer owns
  sus paths nuevos; el orquestador mergea esos vía git auto-merge).
- **Determinism.** Same inputs → byte-identical outputs. Sort by
  (plugin_id, F-id, kind, identifier) consistently. Sort imports by PEP 8.
- **Idempotence.** Apply mismos intents 2x → same result. Intent
  matching existing entry = skip, no error.
- **No new behavior.** NUNCA refactorizar, reordenar existing entries,
  agregar docstrings, "cleanup". Solo applied declared intents.
- **One file at a time.** Process spinal files independently. Failure en
  worker.py no debe corromper composition.py.
- **Restore on failure.** Si spinal file becomes syntactically invalid
  → restore main-state para ese file + record failure. NUNCA dejar file
  broken en worktree.
- **No git.** Orchestrator maneja todo (`git add`, `commit`, `push`,
  rebase).
- **No human-in-the-loop.** Merger corre unattended desde orquestador.
  Si ambiguous, choose safer (skip + warn).
- **Anchor regex es literal.** Si anchor dice `^## Tools` y file tiene
  `## tools` (lowercase), no match. Fallback EOF + warn.
- **Validate antes de declarar success.** Cada modified file debe
  parse / be well-formed. NO "applied pero maybe broken".
- **Imports as sets.** Deduplicate `requires_imports` entre intents. Dos
  intents requiring `from functools import lru_cache` → ONE import line.
- **Reject mid-file mutations.** Wiring intent vocabulary describe
  APPENDS. Si alguna task declaró intent que mutaría existing line (no
  debería — implementer skill bloquea eso), refuse + record error.
- **Protected = HARD STOP.** Cualquier intent target marked
  `protected: true` en spinal-files.yaml → status: failed, abortar
  inmediatamente. Es bug del feature task que no se bloqueó.

---

## §9. Salida final

Escribir `$ARTIFACTS_DIR/merge-report.yaml`.

NO commit / push / git status. El orquestador hace `git add` + commit
después de leer el report.

NO imprimir "next steps". El orquestador decide.

---

**Fin SKILL.**
