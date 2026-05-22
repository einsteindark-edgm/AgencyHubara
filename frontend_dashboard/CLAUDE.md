# frontend_dashboard — contexto frontend React/TS

> Scoped a frontend. Se carga ADEMÁS del `CLAUDE.md` raíz cuando trabajás aquí.
> Detalle canónico en `.claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md` + `06-frontend-plugin.md`.

## FSD — 5 capas (import strict, baja → alta)

```
shared      ← UI primitives, lib, api, config (puede ser importado por todos)
  ↓
entities    ← model + api + contracts + keys + types (queries TanStack)
  ↓
features    ← componentes con lógica de UX/business
  ↓
pages       ← composición page-level
  ↓
app         ← providers, router (composition root)

src/plugins/<id>/frontend/  ← capa transversal: pages/features/entities local al plugin
```

**Regla dura:** import flow es estrictamente bottom-up. Una `feature` puede importar de `entities` y `shared`, NUNCA de `pages` ni de otra `feature` hermana. Enforzado por `dependency-cruiser`.

## Stack canónico

- **Vite** + React 19 + TypeScript (strict, composite con `tsc -b`)
- **TanStack Query** (queries + mutations + invalidation)
- **Zod** (runtime schemas en `entities/<id>/contracts.ts`)
- **Tailwind v4** (`@theme` block en `src/index.css`)
- **Vitest** (unit) + **Playwright** (e2e en `e2e/`)
- **dependency-cruiser** + `tsconfig.arch.json` (architecture gates)

## Plugins frontend actuales

`ads`, `agents_admin`, `catalog`, `chats`, `eta`, `orders`, `system_map` (+ `_schema` que es codegen artifact, no editar).

Cada `src/plugins/<id>/frontend/` tiene su propio mini-FSD: `pages/`, `features/`, `entities/`. El plugin reexporta via `src/plugins/<id>/frontend/index.ts` con un `Page` por defecto.

## 14 anti-patterns FSD (los que ya documentamos)

Detalle en `.claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md`. Los más comunes:

1. Importar de `pages/` desde `features/` (rompe el flow).
2. Cross-feature imports (`features/A` → `features/B`).
3. Lógica de negocio en `pages/` (debe vivir en `features/` o `entities/`).
4. Mutación directa de cache TanStack (usar `setQueryData` con keys del query factory).
5. `useEffect` para data fetching (usar `useQuery`).
6. Componentes que mezclan `data layer` con `presentation`.
7. Reusar el `useQuery` raw en lugar del entity hook custom.
8. Zod schemas duplicados en lugar de uno en `entities/<id>/contracts.ts`.
9. `any` en boundaries de query.
10. Tailwind tokens hardcoded en lugar de `@theme` vars.
11. Importar de `@/shared/ui` con paths absolutos que rompen el barrel.
12. Modificar `shared/ui/Icon.tsx` sin agregar al `iconRegistry` (rompe spinal file contract).
13. Crear `providers` fuera de `app/providers/index.tsx`.
14. Edits ad-hoc a `src/index.css` (debe pasar por el `@theme` block).

## Comandos (todos desde el repo root con `cd frontend_dashboard &&`)

| Acción | Comando |
|---|---|
| Dev server | `cd frontend_dashboard && npm run dev` |
| Sync plugins (codegen) | `cd frontend_dashboard && npm run plugins:sync` |
| Test unit (vitest) | `cd frontend_dashboard && npm test` |
| Test arch (dep-cruiser) | `cd frontend_dashboard && npm run test:arch` |
| Type check (composite) | `cd frontend_dashboard && npx tsc -b` |
| Build prod | `cd frontend_dashboard && npm run build` |
| E2E Playwright | `cd frontend_dashboard && npx playwright test` |

## Paths PROTECTED (no editar sin ADR)

**Spinal files cross-plugin:**
- `src/shared/ui/Icon.tsx` — append-only iconRegistry (kind `ts_object_entries_append`)
- `src/shared/{ui,lib,api,config}/index.ts` — 4 barrel files (kind `ts_barrel`)
- `src/entities/<id>/{index.ts, api.ts, model.ts, contracts.ts, keys.ts}` — entity boundary
- `src/app/providers/index.tsx` — composition root
- `src/index.css` — `@theme` block

**Architecture gates:**
- `src/test/architecture/**` — dep-cruiser + tsc-arch tests
- `.dependency-cruiser.cjs`
- `tsconfig.arch.json`

Modificar PROTECTED silenciosamente rompe CI en `npm run test:arch`.

## Cuando agregar / modificar

- **Feature nueva:** leer `.claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md` (4 import rules + 14 anti-patterns).
- **Plugin frontend nuevo:** sección `06-frontend-plugin.md` + `examples/plugin-frontend-only.md`.
- **Entity nueva:** layout `entities/<id>/{api.ts, contracts.ts, model.ts, keys.ts, index.ts}` + tests `*.test.ts`.
- **Icon nuevo:** appendear a `iconRegistry` de `shared/ui/Icon.tsx` (NO crear archivo nuevo).
- **Tailwind token nuevo:** appendear al `@theme` block en `src/index.css`.

## Gotcha local

- `src/index.css` mide ~93 KB. La mayor parte es el `@theme` block (Tailwind v4). Editar SOLO entre los markers del bloque; tocar fuera rompe variables CSS de otros features.
- `src/plugins/<id>/frontend/index.ts` debe exportar `default { Page }` o `scripts/plugins-sync.ts` skipea el plugin silenciosamente.
- TanStack Query keys viven en `entities/<id>/keys.ts`. Si dos features queryean la misma entity, **comparten el key factory** — no duplicar.
- `tsc -b` (composite) requiere que cada `tsconfig.*.json` esté bien linkeado. Si un import "no resuelve", chequear `tsconfig.json` references primero.
