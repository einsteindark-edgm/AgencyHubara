# Sección 05 — Frontend FSD (capas + plugin registry + entities + features)

> **Cuándo leer esto:** vas a editar `frontend_dashboard/src/{shared,app,
> pages}/`, o las entities de un plugin (`plugins/<id>/frontend/entities/`).
> **Pre-requisito:** `sections/01-general.md`. Si vas a crear/editar el
> frontend de un plugin específico, después leé `sections/06-frontend-plugin.md`.
> **Tamaño:** ~10 KB.
> **Reference complementario:** `references/fsd-rules.md`.

---

## §1. Las 5 capas FSD (de abajo hacia arriba)

```
┌─────────────────────────────────────────────────────┐
│ 5. app/        — providers + plugin-registry        │ ← consume todo lo de abajo
├─────────────────────────────────────────────────────┤
│ 4. pages/      — shells (Dashboard.tsx)             │ ← consume entities + plugins + shared
├─────────────────────────────────────────────────────┤
│ 3. plugins/<id>/frontend/  — features + entities    │ ← consume SUS entities + shared
├─────────────────────────────────────────────────────┤
│ 2. entities/   — VACÍA post-F1-F8 (gate P-11)       │ ← entities viven en cada plugin
├─────────────────────────────────────────────────────┤
│ 1. shared/     — primitivas UI + lib + api          │ ← floor; NO consume nada de src/*
└─────────────────────────────────────────────────────┘
```

**Las 4 import rules** (enforced por `dependency-cruiser`):

1. `shared/` no importa nada de `src/` (es el floor).
2. `entities/` solo importa `shared/` (capa central hoy VACÍA — ver §3).
3. `features/` (legacy) solo importa `entities/` + `shared/` (también vacía).
4. `pages/` y `app/` pueden importar todo lo de abajo.

**Reglas extra del refactor** (también enforced por dep-cruiser):

| Regla | Significa |
|---|---|
| Cross-plugin imports prohibidos | `@plugins/chats/* ❌→ @plugins/orders/*` (incluye entities — gate P-22) |
| Plugins prohibido importar `pages/` o `app/` | El plugin es agnóstico al shell |
| `features/*` (legacy) prohibido importar `plugins/*` | Las features legacy no saben de plugins |
| Excepción documentada | `pages/Dashboard.tsx → app/plugin-registry.generated.ts` (el shell consume el registry) |
| Dentro del mismo plugin, cross-feature OK | Relajación del FSD strict; ver §3.2 abajo |
| Imports intra-plugin SIEMPRE por alias | `@plugins/<id>/frontend/entities/<x>` — `../../` relativo lo prohíbe dep-cruiser (`no-relative-cross-layer`); `@/entities/...` es alias MUERTO (otro gate lo caza) |

Detalle de los **14 anti-patterns FSD** en `references/fsd-rules.md`.

---

## §2. `shared/` — el floor (primitivas + lib)

```
frontend_dashboard/src/shared/
├── ui/                          # primitivas visuales: Icon, Button, Panel, Toolbar, TitleBar, StatusBar
│   ├── Icon.tsx                 # SET BASE de SVG icons compartidos (glifos nuevos van en el plugin — ver §07)
│   ├── Button.tsx
│   ├── Panel.tsx
│   ├── Toolbar.tsx              # segmented control con sections dinámicas (props-driven)
│   ├── TitleBar.tsx             # barra de título macOS-style
│   ├── StatusBar.tsx
│   └── index.ts                 # barrel
├── lib/                         # utils sin DOM
│   ├── IS_DESKTOP.ts
│   ├── cn.ts                    # className helper
│   └── index.ts
├── api/                         # fetch wrapper base + Zod boundary
│   ├── client.ts                # apiClient.get/post/... → wrapper httpx-style
│   ├── sse.ts                   # subscribeSse helper
│   └── index.ts
└── config/                      # env runtime
    ├── env.ts                   # env.apiUrl, env.isDev, etc.
    └── index.ts
```

### Reglas críticas de `shared/`:

- **Zero domain knowledge** — un componente de `shared/` no conoce
  "chat", "order", "agent" (cualquier nombre del dominio).
- **Zero `useState` para server data** — TanStack Query vive en las
  entities del plugin (`plugins/<id>/frontend/entities/`), NO en `shared/`.
- **Sin fetch directo en componentes** — todo HTTP pasa por
  `apiClient.get/post/...` (definido en `shared/api/client.ts`).
- **Zod at the boundary** — cada `apiClient.get<unknown>(...)` se sigue de
  `schema.parse(raw)`. El compile-time `<T>` es documentación; Zod es enforcement.

---

## §3. `entities/` — dominio shared cross-plugin

> **(post-refactor F1-F8)** Las entities ya NO viven en `src/entities/`
> central: cada entity de dominio es **single-owner** y vive dentro de su
> plugin, en `plugins/<id>/frontend/entities/<entity>/`. `src/entities/`
> central DEBE quedar VACÍO — lo enforcea el gate **P-11**
> (`src/test/architecture/test_plugin_entity_ownership.arch.test.ts`).
> Un plugin JAMÁS importa la entity de otro plugin (gate **P-22**); el
> caso cross-plugin va por cast declarado (ver §3.2).

```
frontend_dashboard/src/plugins/
├── chats/frontend/entities/
│   ├── chat/                    # useChatInbox
│   ├── session/                 # useSessionsStream (SSE)
│   ├── message/
│   ├── handoff/
│   └── order-ref/               # entity LOCAL del cast chats→orders (§3.2)
├── orders/frontend/entities/order/        # useOrders
├── eta/frontend/entities/tracked-order/   # useTrackedOrders
└── agents_admin/frontend/entities/agent/  # useAgents
```

Cada entity tiene la estructura típica FSD (mismos roles que antes):

```
plugins/<id>/frontend/entities/<x>/
├── model.ts                     # interface <X> (pure TS types, no Zod)
├── contracts.ts                 # z.object({...}) — Zod schemas
├── keys.ts                      # query key factory: <x>Keys.all / .detail(id) / ...
├── api.ts                       # TanStack Query hooks: use<X>, use<X>List, useStream<X>
├── filters.ts                   # pure predicates (sin DOM, sin hooks)
├── index.ts                     # barrel — exporta surface pública
└── (api.test.tsx, etc.)
```

Imports DENTRO del plugin: SIEMPRE vía alias
`@plugins/<id>/frontend/entities/<entity>` — NUNCA rutas relativas
`../../` (dep-cruiser las prohíbe) ni `@/entities/...` (alias muerto,
otro gate lo caza).

### §3.1 Patrón canónico de una entity

```typescript
// canonical — plugins/<id>/frontend/entities/<x>/model.ts
export interface <X> {
  id: string;
  // ...
}

// canonical — plugins/<id>/frontend/entities/<x>/contracts.ts
import { z } from "zod";
export const <x>Schema = z.object({
  id: z.string(),
  // ...
});
export type <X>Dto = z.infer<typeof <x>Schema>;

// canonical — plugins/<id>/frontend/entities/<x>/keys.ts
export const <x>Keys = {
  all: ["<x>"] as const,
  list: () => [...<x>Keys.all, "list"] as const,
  detail: (id: string) => [...<x>Keys.all, "detail", id] as const,
} as const;

// canonical — plugins/<id>/frontend/entities/<x>/api.ts
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { <x>Keys } from "./keys";
import { <x>Schema } from "./contracts";

// La entity llama SOLO a la API de SU plugin: /api/<plugin>/* (gate P-9).
export function use<X>(id: string | null) {
  return useQuery({
    queryKey: <x>Keys.detail(id ?? ""),
    queryFn: async () => <x>Schema.parse(await apiClient.get<unknown>(`/api/<plugin>/<x>/${id}`)),
    enabled: !!id,
  });
}

// canonical — plugins/<id>/frontend/entities/<x>/index.ts
export { <x>Keys } from "./keys";
export { use<X> } from "./api";
export type { <X> } from "./model";
```

### §3.2 Cuándo agregar a una entity existente vs crear nueva

| Caso | Acción |
|---|---|
| Hook nuevo sobre dato YA modelado (e.g. `useSessionTags(id)`) | Agregar a `plugins/chats/frontend/entities/session/api.ts` |
| Tipo / schema relacionado a dato YA modelado | Agregar a `model.ts` + `contracts.ts` de esa entity del plugin |
| Dato nuevo (e.g. `reservation`) | Crear `plugins/<id>/frontend/entities/reservation/` en el plugin DUEÑO del dato |
| Dato de OTRO plugin que tu plugin necesita | NUNCA importar su entity (P-22). Cast declarado: `depends_on` + `consumes` en tu plugin.yaml, cast server-side en TU api, entity LOCAL que llama solo `/api/<tu-id>/*` (PLUGIN_CONTRACT.md §5.3, canal 3) |

**Regla de oro (post-refactor F1-F8):** cada entity tiene UN solo dueño —
el plugin. No existen entities shared: el caso "2+ plugins consumen el
dato" se resuelve con cast declarado, no con una entity central. Ejemplo
real: `plugins/chats/frontend/entities/order-ref/` consume `order@v1` de
orders vía el cast `hubara_agency/src/plugins/chats/api/order_actions.py`.

---

## §4. `app/` — providers + plugin registry

```
frontend_dashboard/src/app/
├── index.tsx                    # AppProviders chain (QueryClient + Theme + ...)
├── providers/
│   ├── QueryProvider.tsx        # TanStack Query QueryClient + defaults
│   ├── ThemeProvider.tsx        # (si existe)
│   └── index.tsx                # composer
└── plugin-registry.generated.ts # AUTOGEN, gitignored
```

### §4.1 `app/providers/index.tsx` — el spinal del provider chain

```typescript
// canonical — app/providers/index.tsx
import { QueryProvider } from "./QueryProvider";

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <QueryProvider>
      {/* otros providers acá */}
      {children}
    </QueryProvider>
  );
}
```

Si tu task agrega un provider nuevo (e.g. `ThemeProvider`), edita acá
**y declara `wiring_intent` `provider_wrap`** en task-result.yaml (es
spinal — ver `sections/07-shared-files.md`).

### §4.2 `app/plugin-registry.generated.ts` — el registry

Generado por `scripts/plugins-sync.ts`. NO editar a mano. Si necesitás
algo del registry que no está, agregalo al `plugins-sync.ts` (spinal,
muy raro).

Ejemplo del output:

```typescript
// AUTO-GENERATED — DO NOT EDIT
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { icons as etaIcons } from "@plugins/eta/frontend/icons";

export type SectionEntry = { key: string; label: string; order?: number; icon?: string };
export type SidebarEntry = { route: string; label: string; icon?: string; badge_query?: string };

// Protocolo estructural del entry: `@plugins/<id>/frontend` DEBE
// default-exportar un componente. assertPluginModule no hace nada en
// runtime — existe para que `tsc` FALLE EN COMPILACIÓN si un plugin
// rompe su contrato de entry.
type PluginModule = { default: ComponentType<any> };
const assertPluginModule = (m: PluginModule): PluginModule => m;

export type PluginEntry = {
  id: string;
  displayName: string;
  sidebar: SidebarEntry[];
  sections: SectionEntry[];
  dashboardWidgets: Array<{ id: string; position: string }>;
  Page: LazyExoticComponent<ComponentType<any>>;
};

export const PLUGINS: PluginEntry[] = [
  {
    id: "chats",
    displayName: "Chats",
    sidebar: [{ "route": "/chats", "label": "Chats", "icon": "chat" }],
    sections: [{ "key": "chat", "label": "Chats", "order": 1, "icon": "chat" }],
    dashboardWidgets: [],
    Page: lazy(() => import("@plugins/chats/frontend").then(assertPluginModule)),
  },
  // ... uno por plugin habilitado
];

// Glifos aportados por plugins (frontend/icons.tsx) — merge en el shell.
export const PLUGIN_ICONS: Record<string, ComponentType<any>> = {
  ...etaIcons,
};
```

---

## §5. `pages/Dashboard.tsx` — el shell macOS

Es la **única página** del frontend (no hay router multi-página por
ahora). Su contrato es:

1. Lee `PLUGINS` + `PLUGIN_ICONS` del registry generado.
2. Deriva las sections del Toolbar de `PLUGINS.flatMap(p => p.sections)`;
   el Toolbar resuelve íconos contra base (`Icon.tsx`) ∪ contribuciones
   (`PLUGIN_ICONS`) con fallback bot.
3. Indexa `pageByKey: Map<sectionKey, Page>` para resolver qué plugin
   renderizar cuando el operador clickea una section.
4. Renderiza `<ActivePage />` SIN props, envuelto en
   `<PluginHostProvider value={host}>` — el "pluginProps bag" YA NO
   EXISTE; el plugin lee el shell vía `usePluginHost()`/`useSelection()`
   (ver `sections/06-frontend-plugin.md`).

```typescript
// canonical — pages/Dashboard.tsx (estructura, no completo)
import { PLUGINS, PLUGIN_ICONS } from "@/app/plugin-registry.generated";
import { Toolbar } from "@/shared/ui";
import { PluginHostProvider } from "@/shared/lib";
import { Suspense, useCallback, useState, useMemo } from "react";

export function Dashboard() {
  const sections = useMemo(
    () => PLUGINS.flatMap(p => p.sections).sort((a, b) => (a.order ?? 0) - (b.order ?? 0)),
    [],
  );
  const pageByKey = useMemo(() => {
    const m = new Map<string, React.LazyExoticComponent<any>>();
    for (const p of PLUGINS) {
      for (const sec of p.sections) m.set(sec.key, p.Page);
    }
    return m;
  }, []);

  const [section, setSection] = useState(sections[0]?.key ?? "");
  const [showSidebar, setShowSidebar] = useState(true);
  const [showInspector, setShowInspector] = useState(true);

  // PluginHost (F7): contexto GENÉRICO shell↔plugin. La selección
  // cross-sección vive en un mapa clave→id; el shell NO conoce las claves.
  const [selection, setSelectionMap] = useState<Record<string, string | null>>({});
  const setSelection = useCallback((key: string, id: string | null) => {
    setSelectionMap(prev => (prev[key] === id ? prev : { ...prev, [key]: id }));
  }, []);
  const host = useMemo(
    () => ({ showSidebar, showInspector, selection, setSelection }),
    [showSidebar, showInspector, selection, setSelection],
  );

  const ActivePage = pageByKey.get(section);

  return (
    <div className="dashboard-shell">
      <Toolbar sections={sections} section={section} setSection={setSection}
               pluginIcons={PLUGIN_ICONS} /* + toggles sidebar/inspector */ />
      <Suspense key={section} fallback={null}>
        {ActivePage && (
          <PluginHostProvider value={host}>
            <ActivePage />
          </PluginHostProvider>
        )}
      </Suspense>
    </div>
  );
}
```

**NUNCA editar `Dashboard.tsx` para agregar tu plugin** — es 100%
data-driven. Si necesitás un nuevo "tipo" de page que no es section
(e.g. modal global), eso es cambio arquitectural — ADR + PR separado.

---

## §6. `index.css` — Tailwind v4 tokens

`@theme {}` block tiene los tokens globales. Cuando agregás tokens
(e.g. `--color-warn`), editás este archivo (es spinal — ver
`sections/07-shared-files.md`).

```css
/* canonical — src/index.css */
@theme {
  /* tokens existentes */
  --color-fg: #0f172a;
  --color-fg-muted: #475569;
  --color-bg: #ffffff;

  /* tu token nuevo */
  --color-warn: #f59e0b;
  --color-warn-glow: rgba(245, 158, 11, 0.3);
}
```

**Naming rule:** nunca `--color-text-*` (anti-pattern #13 FSD). Usá
`--color-fg`, `--color-fg-muted`.

---

## §7. TanStack Query — patrones canónicos

### §7.1 Query simple

```typescript
const { data, isLoading, error } = useSession(sessionId);
if (isLoading) return <div>Loading…</div>;
if (error) return <ErrorPanel error={error} />;
if (!data) return null;
return <SessionDetail data={data} />;
```

### §7.2 Query con `enabled` (null-tolerant)

```typescript
export function useSession(id: string | null) {
  return useQuery({
    queryKey: sessionKeys.detail(id ?? ""),
    queryFn: async () => sessionSchema.parse(
      await apiClient.get<unknown>(`/api/sessions/${id}`),
    ),
    enabled: !!id,                    // no fetcha si id es null
  });
}
```

### §7.3 SSE stream

```typescript
import { subscribeSse } from "@/shared/api/sse";
import { useQueryClient } from "@tanstack/react-query";

export function useSessionsStream() {
  const qc = useQueryClient();
  useEffect(() => {
    const sub = subscribeSse(`/api/dashboard/sessions/stream`, {
      onMessage(msg) {
        qc.setQueryData(sessionKeys.list(), (prev: Session[] | undefined) => {
          // merge logic
          return mergedList;
        });
      },
    });
    return () => sub.unsubscribe();
  }, [qc]);
}
```

### §7.4 Mutation

```typescript
export function useUpdateOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: UpdateOrderInput) =>
      orderSchema.parse(await apiClient.patch<unknown>(`/api/orders/${input.id}`, input)),
    onSuccess(updated) {
      qc.setQueryData(orderKeys.detail(updated.id), updated);
      qc.invalidateQueries({ queryKey: orderKeys.list() });
    },
  });
}
```

---

## §8. Tests del frontend

- **Vitest** para unit tests. `setupFiles` configurado en `vitest.config.ts`.
- **React Testing Library** para componentes.
- **Mock `fetch`** con `vi.stubGlobal("fetch", fetchMock)` + cleanup en `afterEach`.
- **QueryClientProvider wrapper** con `retry: false` por test (evita cross-test cache leakage).
- **`renderHook + act`** para hooks de estado local.

```typescript
// canonical — entities/<x>/api.test.tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { use<X> } from "./api";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("use<X> fetches and validates with Zod", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ id: "1", name: "foo" }),
  }));
  const { result } = renderHook(() => use<X>("1"), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data?.id).toBe("1");
});
```

---

## §9. Anti-patterns top-5 del frontend

| # | Anti-pattern | Por qué mal | Qué hacer |
|---|---|---|---|
| 1 | `fetch(...)` directo en componente | Viola "no fetch in components" | Usar entity hook (`useX`) |
| 2 | `useState` para server data | Cache se pierde con re-mount | TanStack Query (`useX`) |
| 3 | `from "@plugins/orders/..."` desde `plugins/chats/` | Cross-plugin import — P-22 | Cast declarado + entity LOCAL (§3.2) |
| 4 | `from "@/entities/order"` o `from "../../entities/order"` | Alias muerto / ruta relativa cross-layer (dep-cruiser) | `@plugins/<id>/frontend/entities/order` (alias intra-plugin) |
| 5 | `--color-text-primary` token | Naming anti-pattern #13 | `--color-fg`, `--color-fg-muted` |

Detalle de los **14 anti-patterns** en `references/fsd-rules.md`.

---

## §10. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Crear el frontend de un plugin | `sections/06-frontend-plugin.md` |
| Editar shared file (Icon registry, providers, etc.) | `sections/07-shared-files.md` |
| Diagnosticar fallo `npm run test:arch` | `sections/08-tests-and-gates.md` + `references/fsd-rules.md` |
| Ver ejemplo real de plugin frontend-only | `examples/plugin-frontend-only.md` |

---

**Fin sección 05.**
