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

## Superficie SDK (post F-SDK-0)

`@/shared/sdk` es la superficie CANÓNICA del shell para plugins (espejo TS de
`hubara_agency/src/sdk/`): `usePluginHost`/`useSelection`/`PluginHostProvider`,
`apiClient`/`ApiError`, `subscribeSse`. Código nuevo de plugins importa de ahí
(el resto de `@/shared/*` sigue disponible, pero lo que está en el SDK tiene
contrato de estabilidad + check). Docs: `docs/_sdk/01-fachada-sdk.md`.

## Plugins frontend actuales

`ads`, `agents_admin`, `catalog`, `chats`, `eta`, `marketing`, `mba`, `orders` (+ `_schema` que es codegen artifact, no editar).

Cada `src/plugins/<id>/frontend/` tiene su propio mini-FSD: `pages/`, `features/`, `entities/`. El plugin reexporta via `src/plugins/<id>/frontend/index.ts` con un `Page` por defecto.

## Política de estado y datos (post-refactor F0-F6, auditoría 2026-06-10)

Seis reglas. Los reviewers las exigen; las 1-2 tienen gate automático
(`src/test/architecture/test_realtime_policy.arch.test.ts`).

1. **Server state vive SOLO en TanStack Query** — nunca copiado a
   `useState`/`useEffect`. Los `queryFn` SIEMPRE pasan el `{ signal }` al
   `apiClient` (cancela requests en vuelo al cambiar de sección).
2. **Realtime por push, polling solo como fallback.** UNA conexión SSE por
   app (`EventStreamProvider` → `/api/dashboard/events`); cada plugin se
   suscribe con `useDashboardEvents("<dominio>", handler)` en su entity y
   traduce eventos → `invalidateQueries`/`setQueryData` + monta
   `useInvalidateOnReconnect`. `refetchInterval` permitido únicamente:
   (a) numérico ≥ 60_000 como red de seguridad, (b) function-form acotado a
   un run activo (patrón catalog), allowlisteado en el gate. El lado backend
   publica en `src/platform/events` (bus in-proc del API) o llega solo vía
   el sampler del vault (`chats/api/dashboard.py`).
3. **UI state local y colocado** (`useState` en el componente dueño). Flujos
   multi-paso → `useReducer` + unión discriminada (referencia:
   `ConfirmPaymentAction`). Errores de mutation se DERIVAN de la mutation
   (referencia: `ReadyForShip`), no se duplican en `useState`.
4. **Estado cross-sección SOLO vía PluginHost** — `useSelection(pluginId,
   fallback)`; el fallback lo declara el plugin, el shell no siembra ids.
5. **Derivados de reloj se computan en render** con utils puras
   (`@/shared/lib` dates: `todayIso`/`addDaysIso`) — nunca en mappers ni
   queryFn (la cache debe ser estable respecto al tiempo; `overdue` viene
   del backend).
6. **Cero diálogos JS nativos** (`window.confirm/alert`) — no son confiables
   en los webviews de Tauri. Confirmaciones: inline de dos pasos (patrón
   DangerPanel) o modal propio.

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
12. Meter un glifo per-plugin en `shared/ui/Icon.tsx` — el plugin lo trae en su `frontend/icons.tsx` (post-F7; `Icon.tsx` es solo el set base compartido).
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

## Paths PROTECTED (post-refactor F1-F8: fuente ÚNICA)

La lista de paths protegidos vive en **`hubara_agency/.hubara/spinal-files.yaml`**
(entries `protected: true`) — el meta-gate de este stack
(`src/test/architecture/helpers.ts`) y el del backend la DERIVAN de ahí.
Para este stack hoy cubre: `src/test/architecture/**`, `.dependency-cruiser.cjs`,
`tsconfig.arch.json` (+ workflows/skills del pipeline). Editar un protected:
local con `ARCH_CHANGE_APPROVED=1`; el PR lleva el label `architecture-change`.

Spinal files mergeables (append-only, NO requieren label pero sí cuidado):
`src/shared/{ui,lib,api,config}/index.ts` (barrels), `src/app/providers/index.tsx`,
`src/index.css` (@theme block), `src/shared/ui/Icon.tsx` (solo glifos
genuinamente compartidos — los per-plugin van en el plugin, ver abajo).

## Cuando agregar / modificar (post-refactor F1-F8)

- **Feature nueva:** leer `.claude/skills/hubara-architecture-guide/sections/05-frontend-fsd.md` (4 import rules + 14 anti-patterns).
- **Plugin frontend nuevo:** sección `06-frontend-plugin.md` + `examples/plugin-frontend-only.md`. Checklist completo: `PLUGIN_CONTRACT.md` §6 + `PLUGIN_PROTOCOL_fable.md`.
- **Entity nueva:** vive EN EL PLUGIN — `src/plugins/<id>/frontend/entities/<entity>/{api.ts, contracts.ts, model.ts, keys.ts, index.ts}` + tests. `src/entities/` central DEBE quedar vacío (gate P-11). Imports con alias `@plugins/<id>/frontend/entities/<entity>` (ni relativos `../../` ni `@/entities/` — gates los cazan).
- **Datos de OTRO plugin:** NUNCA importar su entity (gate P-22). Declarar `consumes:` + `depends_on:` en el manifest y servir un cast server-side bajo `/api/<tu-id>/*` (ejemplos: `chats/api/order_actions.py`, `agents_admin/api/evals.py`).
- **Icon nuevo:** el plugin lo TRAE en `src/plugins/<id>/frontend/icons.tsx` (`export const icons = { nombre: Componente }`) + `npm run plugins:sync`. Cero ediciones a `Icon.tsx` (gate P-12 valida base ∪ contribuciones).
- **Chrome/selección del shell:** los Pages NO reciben props — `usePluginHost()` + `useSelection("<plugin-id>")` desde `@/shared/lib`. `Dashboard.tsx` no se toca.
- **Tailwind token nuevo:** appendear al `@theme` block en `src/index.css`.

## Gotcha local

- `src/index.css` mide ~93 KB. La mayor parte es el `@theme` block (Tailwind v4). Editar SOLO entre los markers del bloque; tocar fuera rompe variables CSS de otros features.
- `src/plugins/<id>/frontend/index.ts` debe **default-exportar el componente Page** — el registry generado lo verifica EN COMPILACIÓN (`assertPluginModule`); sin bloque `frontend:` en el manifest, plugins-sync lo skipea con info-log.
- TanStack Query keys viven en `plugins/<id>/frontend/entities/<entity>/keys.ts`. Si dos features del MISMO plugin queryean la misma entity, **comparten el key factory** — no duplicar.
- `tsc -b` (composite) requiere que cada `tsconfig.*.json` esté bien linkeado. Si un import "no resuelve", chequear `tsconfig.json` references primero.
- Correr `npm run test:arch` tras tocar un PROTECTED requiere `ARCH_CHANGE_APPROVED=1` en el env (el meta-gate diffea contra origin/main).

## Verificación visual — SIEMPRE usar el stack Docker desplegado (NO levantar un vite suelto)

**Antes de abrir el navegador para verificar tu trabajo, corré `docker ps`.** El stack ya está desplegado en Docker; ahí están los puertos REALES. NO levantes un `vite` / backend a mano: chocás con otros procesos y perdés tiempo (ver abajo).

| Servicio | Container | URL real | Qué es |
|---|---|---|---|
| **Frontend** | `local-hubara-frontend` | **http://localhost:5174** | Vite **dev con HMR** sobre bind-mount del source (`5174→5173` del container) |
| API | `local-hubara-api` | http://localhost:8000 | FastAPI (el `VITE_API_URL` del front apunta acá) |
| Temporal UI | `local-temporal-ui` | http://localhost:8233 | Ver runs/queries de los workflows (ej. `CatalogSyncWorkflow`) |
| Temporal | `local-temporal` | localhost:7233 | gRPC para `get_temporal_client()` |

- **El frontend HMR-ea el source bind-mounteado** (`docker inspect local-hubara-frontend` → Mounts). Hoy ese mount es el checkout **`main`** (`/Users/.../AgencyHubara/frontend_dashboard`), NO un worktree. Editar `frontend_dashboard/src` en **main** se refleja en vivo en `:5174` — sin rebuild, sin dev server propio.
- **Trabajando en un WORKTREE:** `:5174` sigue sirviendo `main`, así que NO ves tus cambios del worktree ahí. Para verlos: o trabajás en main, o re-apuntás el bind-mount del container al worktree (`docker compose -f hubara_agency/docker-compose.local.yml up -d --no-deps --build hubara-frontend` desde el worktree) — pero eso **reinicia un servicio del stack vivo del usuario, pedí OK primero**.
- Rebuild de un servicio puntual: `cd hubara_agency && docker compose -f docker-compose.local.yml up -d --build <servicio>` (el compose se regenera con `uv run python scripts/render-compose.py`). El backend (`hubara-api` + workers) necesita rebuild para tomar cambios de `.py`; el frontend no (HMR).
- **NO levantar `vite` suelto en :5173** — el usuario corre el Vite de **Archon** en `[::1]:5173` (IPv6). Un `vite --port 5173` del worktree bindea `*:5173` (IPv4) y NO colisiona → el browser entra por `localhost`→`::1`→**carga Archon, no tu dashboard**. Si DEBÉS usar tu propio vite, navegá a `http://127.0.0.1:5174/5173` (IPv4 explícito). Ver [[local_dual_backend_localhost_race]] en memoria.
- Las secciones del dashboard se cambian por el **toolbar in-app** (click en "Catalog"), no por URL `/catalog` fresca (deja el root vacío — el SPA setea section por estado).
- Los workers (ej. `local-hubara-worker-catalog-sync`) corren con credenciales reales (Medusa/Meta). Disparar un sync real desde `:5174` es **outward-facing** (push a Meta Commerce Catalog) — confirmá antes.
