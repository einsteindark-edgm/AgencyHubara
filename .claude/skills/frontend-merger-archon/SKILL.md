---
name: frontend-merger-archon
description: Consolidates wiring_intents from N parallel implementer agents (one batch of the DAG) into the canonical spinal files of an FSD frontend repo (pages/*.tsx, app/providers/index.tsx, index.css, entity/feature/shared barrels, entities/*/{model,contracts,keys,api}.ts when shared). Designed exclusively for invocation from Archon's implementar-hu orchestrator workflow after every implementar-tarea in a batch finishes. Reads .frontend/spinal-files.yaml plus the N task-result.yaml files staged in $ARTIFACTS_DIR/batch-results/, applies every intent deterministically (sorted by F-id, then by kind, then by identifier), writes consolidated spinal files in the current worktree (the "merge worktree" branched fresh from main), and emits $ARTIFACTS_DIR/merge-report.yaml. Does NOT write feature code, run tests, or commit/push - the orchestrator handles git. Triggers - invoked via Archon workflow skills field; not intended for direct slash command use.
---

frontend-merger-archon — Wiring intent consolidator for parallel batches (FSD frontend)
You are a specialized merger for FSD frontend pipelines (React 19 + TanStack Query + Zod + Tailwind v4 + Feature-Sliced layout). The implementar-hu orchestrator has just finished running N implementer agents in parallel (one per task in a batch). Each agent emitted a task-result.yaml with `wiring_intents` describing what it added to spinal files. Your job is to apply every intent to a fresh "merge worktree" (branched from main) deterministically, producing conflict-free consolidated spinal files.

You DO modify spinal files (pages/<X>.tsx, app/providers/index.tsx, src/index.css, entity barrels, feature barrels, shared barrels, occasionally entities/<x>/{model,contracts,keys,api}.ts when 2+ tasks add to them) in the current worktree. You apply intents FROM SCRATCH onto main-state — you do NOT consume the implementer worktrees' diffs.

You do NOT touch new files (each implementer worktree owns its own new files; the orchestrator merges those separately because they don't conflict).
You do NOT run tests (orchestrator runs them post-merge).
You do NOT git commit/push/branch/rebase (orchestrator owns git).
You do NOT iterate (no $LOOP_USER_INPUT). One pass. If something is ambiguous, skip safely + warn in the report.

Invocation contract (Archon workflow)

You operate inside an Archon workflow execution context with these guarantees:

- $ARTIFACTS_DIR/batch-results/F<NN>-result.yaml contains the task-result.yaml from every implementer in the batch. The orchestrator staged them there before invoking you.
- $ARTIFACTS_DIR/spinal-files.yaml is a copy of the frontend's <frontend_root>/.frontend/spinal-files.yaml. The orchestrator staged it there. Read FROM HERE, not from the frontend root directly (avoids hardcoding the frontend path in the skill).
- The current worktree is the "merge worktree" — pristine, branched fresh from main. Spinal files at their main-state in the worktree (paths like `frontend_dashboard/src/pages/Dashboard.tsx` relative to repo root). NO implementer's diffs applied.
- $USER_MESSAGE tells you the batch_id and task_ids, e.g. "B2 [F02, F04, F05]".
- Your outputs:
  - Modified spinal files in the worktree
  - $ARTIFACTS_DIR/merge-report.yaml summarizing what you applied

Step 1 — Load and validate

1. Parse $USER_MESSAGE to extract batch_id and the expected task_ids list.
2. Read $ARTIFACTS_DIR/spinal-files.yaml. For each entry, note `path` (may glob) and `kind`.
3. Read every $ARTIFACTS_DIR/batch-results/F<NN>-result.yaml in the expected list.
   - If any expected file is missing → abort with status: failed; reason: "expected F<NN>-result.yaml missing from batch-results/".
   - If any task-result has status != passed AND != passed_with_warnings → abort with status: failed; reason: "task F<NN> reported status <X>; cannot merge a non-green batch".
4. Aggregate all wiring_intents into a dictionary:
     intents_by_file = {
       "<spinal_path>": [ (F-id, intent_dict), ... ]
     }
   Sort each list by (F-id ascending, then intent index within the task's result).
5. Validate every spinal_path:
   - Matches an entry in $ARTIFACTS_DIR/spinal-files.yaml (kind is known). Use glob expansion (e.g. `frontend_dashboard/src/pages/*.tsx` matches `frontend_dashboard/src/pages/Dashboard.tsx`).
   - If a path is referenced but NOT in spinal-files.yaml → abort with status: failed; reason: "F<NN> declared intent for <path> not listed in spinal-files.yaml; planner mis-classified the task".

Step 2 — Apply intents per spinal file

For each spinal_path with intents (process files independently; one bad file does not block others):

A. Resolve the actual file path (expand glob if `path: src/pages/*.tsx`; emit a separate result per matched file).

B. Read the current file content (main-state). If the file does NOT exist:
   - Create an empty buffer.
   - The intents will populate it. The implementer task that owns this file's CREATION should have left a stub on main; if not, that's the planner's bug. Record a warning and continue.

C. Aggregate `requires_imports` from every intent for this file:
   - Deduplicate (set semantics).
   - For TypeScript/TSX files, group into:
     - node-builtins (e.g. `import path from "node:path"` — rare in frontend, mostly config files): first.
     - third-party (`react`, `@tanstack/react-query`, `zod`, `lucide-react`, etc.): second.
     - local with `@/` alias: third.
     - relative imports (`./`, `../`): fourth.
   - Sort alphabetically within each group (by the source module string).
   - Insert at the top of the file:
     - After any existing JSDoc top comment block (`/** ... */`).
     - Before the first non-import statement.
     - Maintain a blank line between import groups.
   - For CSS files (`src/index.css`): there are no imports per-intent; tokens are added inside the existing `@theme { ... }` block. Skip the import aggregation step entirely.

D. Apply each intent in order. See "Application rules by kind" below.

E. Validate the result:
   - `.ts` / `.tsx` files: run `npx tsc --noEmit -p tsconfig.app.json` on a temporary tsconfig that includes only the modified file. If the type check fails for the modified file specifically, restore the file to main-state, record the error in errors[], and continue with other files. (Note: types from new files created by parallel tasks may not yet be visible — defer holistic type-check to the orchestrator post-merge.)
   - As a lighter syntactic guard, parse with `node --check <file>` for `.ts` files where applicable, or use the TypeScript compiler API via a quick parse step. At minimum: confirm the file does not contain raw `<<<<<<<` conflict markers.
   - `.css` files: confirm `@theme { ... }` block remains balanced (one `{` per matching `}`).
   - `.md` files (if any in spinal-files.yaml for frontend, e.g. docs): confirm new headings start with `#`.

F. Track for the merge report: intents_applied, intents_skipped, new_imports_added.

Application rules by kind

page_feature_mount  (target: page_jsx_composition files like src/pages/<X>.tsx)
  - Locate the `container_anchor` JSX string (e.g. `<div className="col-right glass-panel">`). Find its line.
  - For each intent in (alphabetical_by_component) order (or `append` per order_hint):
    - Build the JSX line: `<<Component> <props> />` indented to match siblings inside the container (4 or 6 spaces, depending on the page's existing indent).
    - If this exact `<<Component>` JSX element already appears inside the container → skip (idempotent), count toward intents_skipped.
    - Else insert as the last child of the container (before its closing `</div>` or equivalent). Preserve indentation. Self-closing tag.
  - The aggregated import lines from `requires_imports` go at the top of the file per Step 2C.

provider_wrap  (target: app_provider_composition files like src/app/providers/index.tsx)
  - Locate the existing `<AppProviders>` JSX body in the file (typically `<QueryProvider>{children}</QueryProvider>` or chained).
  - For each intent (process all intents for this file in a deterministic outer-to-inner order: outer-positioned providers wrap the existing chain, inner-positioned providers go inside the innermost wrapping):
    - If `order_position: "outer"`: wrap the entire existing chain with `<<ProviderName>>...</<ProviderName>>`.
    - If `order_position: "inner"`: replace the innermost `{children}` with `<<ProviderName>>{children}</<ProviderName>>`.
    - If a provider with the same name already appears in the chain → skip (idempotent).
  - Apply all "outer" intents before "inner" intents; within each group, sort by provider_name alphabetically for determinism.
  - Aggregated imports go at the top of the file per Step 2C.

tailwind_token  (target: css_theme_block files like src/index.css)
  - Locate the `@theme {` opening line and the matching `}` closing brace.
  - For each intent in (alphabetical_by_name) order:
    - Build the line: `  --<name>: <value>;` (2-space indent inside the @theme block).
    - If a line with the same `--<name>:` prefix already exists in the @theme block → skip (idempotent), count toward intents_skipped. Do NOT compare values; same-name-different-value is treated as the implementer needing to block (the merger does not detect it because it cannot read parallel diffs).
    - Else insert before the closing `}`, grouped by `category` if multiple categories are present (color tokens together, then font tokens, then spacing tokens), with one blank line between categories. Preserve existing blank lines and comments inside the @theme block.

barrel_export  (target: ts_barrel files like entities/<x>/index.ts, features/<x>/index.ts, shared/<area>/index.ts)
  - For each intent in declaration order (or `append` per order_hint):
    - Build the export line from `export_statement` (verbatim).
    - If an identical export line already exists in the file → skip (idempotent).
    - If an export with the same identifier (`Foo` in `export { Foo } from "./api"`) but a different source path already exists → record in errors[], abort this file (restore to main-state). The implementer should have blocked.
    - Else append at the end of the file (after the last existing export line).
  - Preserve the JSDoc block at the top if present.
  - No import aggregation for barrels (they use re-exports, not imports).

zod_schema_def  (target: ts_dataclass_module files like entities/<x>/contracts.ts)
  - Append the full `definition` block at the end of contracts.ts.
  - Sort alphabetically by `name` among new additions.
  - One blank line between schemas.
  - Same-name collision: same byte content → skip (idempotent); different → error, restore.
  - Aggregated imports (typically `import { z } from "zod";`) go at the top per Step 2C.

query_key_extension  (target: ts_factory_module files like entities/<x>/keys.ts)
  - Locate the `<factory_name>` const declaration body (e.g. `export const sessionKeys = { ... } as const;`).
  - For each intent (alphabetical_by member_name) order:
    - Insert the `<member_name>: ...` line inside the object literal, before the closing `}`.
    - Preserve trailing commas. Match indentation of sibling members.
    - If the same `<member_name>` already exists with the same definition → skip; with a different definition → error, restore.

ts_type_def  (target: ts_dataclass_module files like entities/<x>/model.ts)
  - Append the full `definition` block at the end of the file.
  - Sort alphabetically by `name` among new additions.
  - One blank line between type definitions.
  - Same-name collision: same content → skip; different → error, restore.

hook_export  (target: ts_factory_module files like entities/<x>/api.ts)
  - Append the full `definition` block at the end of the file.
  - Sort alphabetically by `name` among new additions.
  - One blank line between hooks.
  - Aggregated imports go at the top per Step 2C.
  - Same-name collision: same content → skip; different → error, restore.

Step 3 — Emit merge report

Write $ARTIFACTS_DIR/merge-report.yaml with:

version: 1
batch_id: B<k>
task_ids: ['F02', 'F04', 'F05']
merger: frontend-merger-archon
date: <ISO 8601, e.g. 2026-05-11T14:30:00Z>
status: ok | partial | failed
spinal_files:
  - path: src/pages/Dashboard.tsx
    intents_applied: 3
    intents_skipped: 0            # already present (idempotent)
    intents_errored: 0
    new_imports_added: 3
    validation: passed
  - path: src/index.css
    intents_applied: 4
    intents_skipped: 1
    intents_errored: 0
    new_imports_added: 0          # CSS — no imports
    validation: passed
  - path: src/app/providers/index.tsx
    intents_applied: 1
    intents_skipped: 0
    intents_errored: 0
    new_imports_added: 1
    validation: passed
errors: []
warnings:
  - "container_anchor '<div className=\"col-far-right\">' not found in src/pages/Dashboard.tsx for F04's intent; appended as last child of the top-level <div>"
notes: |
  Free-form notes about non-trivial decisions (e.g., "F02 and F05 both added the same Tailwind token --color-warn with identical values; deduplicated to one").

Status values:

- ok — every intent applied (or idempotently skipped), every file passes validation.
- partial — at least one file was restored to main-state due to validation failure or same-name-different-content collision. The orchestrator should NOT merge this batch to main without operator review.
- failed — fatal precondition error (missing result file, missing spinal entry, etc.). Nothing was modified. Orchestrator must abort the batch.

After writing the report, print to the user a 5-line summary:
  - batch_id
  - # spinal files touched
  - # intents applied
  - # intents skipped (idempotent) / errored
  - overall status

Style rules

- Spinal files only. Never touch new files — they belong to per-task worktrees. The orchestrator merges those independently.
- Determinism. Same inputs → same byte-for-byte outputs. Sort intents by (F-id, kind, identifier) consistently. Sort imports by group + alphabetical.
- Idempotence. Applying the same intents twice produces the same final file. An intent matching an already-present entry is a skip, not an error.
- No new behavior. Do not refactor, reorder existing entries, add JSDoc, prettier-format, or "clean up" while merging. The merger applies declared intents; nothing else.
- One file at a time. Process spinal files independently. A failure in pages/Dashboard.tsx must not corrupt src/index.css.
- Restore on failure. If a spinal file becomes syntactically invalid, restore main-state for that file and record the failure. Never leave a broken file in the worktree.
- No git. Do not commit, push, branch, rebase, stash, tag. The orchestrator handles all git operations.
- No human in the loop. The merger runs unattended within the orchestrator. If ambiguous, choose the safer option (skip + warn), then let the orchestrator surface to the human via merge-report.yaml.
- container_anchor matching is literal. If the intent says `<div className="col-right glass-panel">` and the page has `<div className="col-right  glass-panel">` (double space), they don't match. Fall back to appending as the last child of the top-level container + warn. Do not normalize whitespace.
- Validate before declaring success. Every modified file must be syntactically valid (no conflict markers, balanced braces, parses with TS / CSS parser). No "applied but maybe broken" status.
- Empty spinal files are fine. If a spinal file doesn't yet exist in main and the batch wants to introduce content, create it. For pages/<X>.tsx that's typically not the case (the page is the entry point and always exists post-scaffold). For index.css the @theme block must already exist; if not, error.
- Imports as sets. Deduplicate `requires_imports` across intents. Two intents both requiring `import { useQuery } from "@tanstack/react-query";` produce ONE import line.
- Reject mid-file mutations. The wiring intent vocabulary describes APPENDS / WRAPS / INSERTS-BEFORE-CLOSE-BRACE. If a task somehow declared an intent that would mutate an existing line (it shouldn't — the implementer skill blocks that), refuse it and record an error.
- Preserve formatting. Match the existing indentation of the spinal file (2 spaces or 4 spaces — read it once, use it consistently within that file). Match existing trailing-comma style in barrel exports and object literals.
- TSX vs TS. Files ending in `.tsx` may contain JSX; `.ts` may not. If a `page_feature_mount` intent targets a `.ts` file, that's a planner error — refuse with an explicit message.
- Provider order matters. The order of provider wraps changes runtime behavior (e.g. an AuthProvider must wrap a feature that calls useAuth(), so AuthProvider is outer). Always respect `order_position` (outer/inner); within the same position, alphabetical is acceptable because providers at the same nesting level are commutative in React.
- Tailwind @theme block tolerance. If the @theme block has comments (`/* ... */`) inside it, preserve them when inserting new tokens; insert tokens after the last existing token line, before the closing brace.
- No prettier/ESLint runs. The merger does not invoke any formatter. The orchestrator runs lint/format post-merge if configured.
