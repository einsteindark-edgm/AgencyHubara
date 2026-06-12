# Project context — AgencyHubara / frontend_dashboard

This file is read by every skill in the frontend pipeline (refiner, planner,
implementer, merger) to know the concrete layout of THIS project. It is copied
to `$ARTIFACTS_DIR/project-context.md` by each workflow's `cargar-*` node.

## Repo layout

- Repo root: `/Users/edgm/Documents/Projects/AgencyHubara/` (the directory that
  contains `.archon/`, `.claude/`, `hubara_agency/`, `frontend_dashboard/`, etc.)
- This repo hosts MULTIPLE projects side-by-side:
  - `hubara_agency/` ← Python uv workspace (exoclaw-temporal agents)
  - `frontend_dashboard/` ← React 19 + Vite frontend (the focus of THIS pipeline)
  - `exoclaw-temporal/` ← the framework (do NOT modify)
- The frontend has its OWN `package.json`, `vite.config.ts`, `tsconfig.app.json`
  — they live at `frontend_dashboard/` root, NOT at repo root.

## Frontend layout (FSD — Feature-Sliced)

```
frontend_dashboard/
├── .frontend/
│   ├── refinements/<HU-id>-{original,tech}.md      ← refinar-hu-frontend persists here
│   ├── plans/<HU-id>/{plan-manifest.yaml,tareas/}  ← planificar-hu-frontend persists here
│   ├── results/<HU-id>/F<NN>-result.yaml           ← implementar-tarea-frontend persists here
│   ├── spinal-files.yaml                            ← convention (read by planner/merger)
│   ├── github-project-config.yaml                   ← Project IDs (optional, for pipeline)
│   └── project-context.md                           ← THIS FILE
├── package.json                       ← dependencies + scripts
├── vite.config.ts                     ← Vite + Tailwind v4 plugin
├── tsconfig.app.json                  ← path alias @/ → src/
├── vitest.config.ts                   ← test env vars (VITE_API_URL, etc.)
├── index.html
└── src/
    ├── main.tsx                       ← entry point (rarely changes)
    ├── index.css                      ← Tailwind @theme tokens                ← SPINAL
    ├── app/
    │   ├── providers/
    │   │   ├── index.tsx              ← AppProviders composition              ← SPINAL
    │   │   └── QueryProvider.tsx
    │   └── layout/                    ← (empty placeholder for shared layouts)
    ├── pages/
    │   └── Dashboard.tsx              ← composition root for the dashboard    ← SPINAL
    ├── features/<name>/               ← user-facing capabilities
    │   ├── ui/<Feature>.tsx           ← root component
    │   ├── ui/<SubComponent>.tsx      ← internal pieces
    │   ├── model/use<X>.ts            ← local-state hooks
    │   └── index.ts                   ← barrel (exports root component only)
    ├── entities/<name>/               ← domain models + data-fetching
    │   ├── model.ts                   ← TS types
    │   ├── contracts.ts               ← Zod schemas
    │   ├── keys.ts                    ← query key factory
    │   ├── api.ts                     ← TanStack Query hooks
    │   ├── filters.ts                 ← pure predicates (optional)
    │   └── index.ts                   ← barrel
    ├── shared/
    │   ├── api/{client,sse,index}.ts  ← apiClient, subscribeSse, ApiError
    │   ├── config/{env,index}.ts      ← env vars
    │   ├── ui/<Primitive>.tsx         ← generic UI (Button, Modal, ...)
    │   ├── lib/<helper>.ts            ← generic helpers
    │   └── hooks/<useX>.ts            ← generic hooks
    └── test/setup.ts                  ← Vitest setup
```

## Path conventions for skills

When you write paths in the refinement / plan / task / wiring_intents:

| Layer | Path FROM REPO ROOT | TS import |
|-------|---------------------|-----------|
| Page | `frontend_dashboard/src/pages/<X>.tsx` | (rarely imported; pages mount features) |
| Feature root | `frontend_dashboard/src/features/<x>/ui/<X>.tsx` | (internal to feature) |
| Feature barrel | `frontend_dashboard/src/features/<x>/index.ts` | `import { X } from "@/features/<x>";` |
| Feature local hook | `frontend_dashboard/src/features/<x>/model/use<Y>.ts` | (internal) |
| Entity types | `frontend_dashboard/src/entities/<x>/model.ts` | (via barrel) |
| Entity schemas | `frontend_dashboard/src/entities/<x>/contracts.ts` | (via barrel) |
| Entity keys | `frontend_dashboard/src/entities/<x>/keys.ts` | (via barrel) |
| Entity hooks | `frontend_dashboard/src/entities/<x>/api.ts` | (via barrel) |
| Entity barrel | `frontend_dashboard/src/entities/<x>/index.ts` | `import { useX, xKeys } from "@/entities/<x>";` |
| Shared api | `frontend_dashboard/src/shared/api/client.ts` | `import { apiClient } from "@/shared/api/client";` |
| Shared UI | `frontend_dashboard/src/shared/ui/<X>.tsx` | `import { X } from "@/shared/ui";` |
| Shared lib | `frontend_dashboard/src/shared/lib/<x>.ts` | `import { fn } from "@/shared/lib";` |
| Tailwind tokens | `frontend_dashboard/src/index.css` (inside `@theme { ... }`) | (CSS, no import) |
| Test (component) | `frontend_dashboard/src/features/<x>/ui/<X>.test.tsx` | (colocated) |
| Test (hook) | `frontend_dashboard/src/features/<x>/model/use<Y>.test.ts` | (colocated) |
| Test (entity) | `frontend_dashboard/src/entities/<x>/api.test.tsx` | (colocated) |

**Import alias trick**: TS imports use the `@/` alias which resolves to
`frontend_dashboard/src/` (configured in `vite.config.ts` + `tsconfig.app.json`).
DO NOT write `import ... from "frontend_dashboard/src/..."` — only `@/...`.
Tests inherit the same alias via `vitest.config.ts`.

## Command conventions (CWD-sensitive)

The operator invokes `archon workflow run <workflow>` from REPO ROOT. The
workflow's CWD inside the worktree is REPO ROOT. But `npm` / `npx` / `vitest`
need to run with CWD = `frontend_dashboard/` because:
  - `package.json` (with `vite`, `vitest`, `@tanstack/react-query`, etc.) is at `frontend_dashboard/`.
  - `vitest` discovers `src/` relative to its CWD.
  - `tsc -b` reads `tsconfig.app.json` from CWD.

ALWAYS prefix verification commands with `cd frontend_dashboard &&`:

```bash
# Correct
cd frontend_dashboard && npm test
cd frontend_dashboard && npm test -- entities/session
cd frontend_dashboard && npm test -- features/session-list
cd frontend_dashboard && npx tsc -b
cd frontend_dashboard && npm run build
cd frontend_dashboard && npm run lint

# Wrong — will fail
npm test                                # no package.json at repo root
cd .. && npm test                       # ambiguous CWD
```

## Test discovery patterns

- `npm test` (root command, runs Vitest) — runs the FULL suite under `src/`.
- `npm test -- entities/session` — runs only tests under `src/entities/session/`.
- `npm test -- features/session-list/model` — narrower scope.
- `npm test -- --reporter=verbose` — see each test name.

For type-checking:
- `npx tsc -b` — incremental build, fastest.
- `npm run build` — full prod build (catches issues that escape `tsc -b`).

For lint:
- `npm run lint` — ESLint on the whole `src/`.

## FSD compliance greps (cheap but mandatory)

These greps verify the 4 import rules + the most common anti-patterns. The
implementer MUST run them after editing:

```bash
cd frontend_dashboard

# Rule: no rogue fetch outside entities/*/api.ts or shared/api/
grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:"

# Rule: no deep imports of features (always go through the barrel)
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features

# Rule: no cross-feature imports (features/a importing features/b)
grep -rEn "from ['\"]@/features/" src/features \
  | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1"

# Rule: no useState + useEffect + fetch combo (manual fetching outside hooks)
grep -rEn "useState.*useEffect.*fetch" src/features src/pages
```

All of these should return EMPTY for a compliant change.

## Available frontends (for HU targeting)

- `frontend_dashboard`: agent monitoring dashboard (currently the only frontend).
  - Pages: `Dashboard.tsx` (3-column session list / chat / metadata).
  - Entities: `session`, `message`.
  - Features: `session-list`, `session-chat`, `session-metadata`, `memory-modal`.

If a future HU introduces a second frontend (e.g. `frontend_admin/`), this file
must be updated to declare it, and `spinal-files.yaml` must cover both.

## Backend that the frontend consumes

- Backend: `hubara_agency/src/dashboard/` (FastAPI).
- Common endpoints:
  - `GET /api/dashboard/sessions` — list
  - `GET /api/dashboard/sessions/:id` — detail
  - `GET /api/dashboard/events` — SSE multiplexado por dominio (F1 2026-06;
    reemplazó a `/stream`). El frontend NO abre EventSource propio: se
    suscribe con `useDashboardEvents("<dominio>", handler)` de `@/shared/api`
    (gate: `test_realtime_policy.arch.test.ts`).
- When a HU adds/changes an endpoint, refer to the backend file in the
  refinement's §6 (Backend contract dependencies). The frontend HU is BLOCKED
  until the backend ships.
