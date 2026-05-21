# Reference — FSD: 4 import rules + 14 anti-patterns

> **Cuándo leer esto:** entendés FSD en general (sección 05) pero
> necesitás el detalle exacto de cada regla y los 14 anti-patterns que
> el linter blocks.
> **Source of truth:** `frontend_dashboard/.dependency-cruiser.cjs` +
> `frontend_dashboard/src/test/architecture/*`.

---

## §1. Las 4 import rules de FSD

```
┌─────────────────────────────────────────┐
│ 5. app/                                  │ ← imports todo lo de abajo
├─────────────────────────────────────────┤
│ 4. pages/                                │ ← imports entities + plugins + shared
├─────────────────────────────────────────┤
│ 3. plugins/<id>/frontend/  (features)    │ ← imports entities + shared
├─────────────────────────────────────────┤
│ 2. entities/                             │ ← imports shared
├─────────────────────────────────────────┤
│ 1. shared/                               │ ← NO imports de src/*
└─────────────────────────────────────────┘
```

### §1.1 Las 4 reglas

| # | Regla | Enforcement |
|---|---|---|
| 1 | `shared/` no importa de `src/*` (es el floor) | dep-cruiser `shared-no-upstream` |
| 2 | `entities/` solo importa `shared/` | dep-cruiser `entities-only-shared` |
| 3 | `plugins/<id>/frontend/` solo importa `entities/` + `shared/` (NO `pages/`, `app/`, ni otros plugins) | dep-cruiser `plugins-isolated` |
| 4 | `pages/` + `app/` pueden importar todo lo de abajo | (sin regla, es la cima) |

### §1.2 Excepción documentada

`pages/Dashboard.tsx → app/plugin-registry.generated.ts` está permitido.
Es la única excepción al strict layering — el shell consume el registry
generado para descubrir plugins.

---

## §2. Los 14 anti-patterns (orden de severidad alta → baja)

### §2.1 — Cross-plugin imports (CRITICAL)

```typescript
// ❌ plugins/chats/frontend/ChatsSection.tsx
import { OrderCard } from "@plugins/orders";          // CRITICAL: cross-plugin

// ✅ Fix: si necesitás OrderCard cross-plugin, promovelo a shared/ui/
import { OrderCard } from "@/shared/ui";
```

**Linter:** dep-cruiser `plugins-no-cross-plugin`.

### §2.2 — Deep imports (CRITICAL)

```typescript
// ❌ Deep import — bypassa el barrel
import { SearchProducts } from "@plugins/chats/frontend/features/sales/ui/SearchProducts";

// ✅ Usar barrel
import { SearchProducts } from "@plugins/chats/frontend/features/sales";
// O mejor, si la feature lo export-default-ea via plugin barrel:
import { ChatsSection } from "@plugins/chats";
```

**Linter:** dep-cruiser `no-deep-imports`.

### §2.3 — `fetch()` directo en componentes (HIGH)

```typescript
// ❌ feature/<x>/ui/Component.tsx
const data = await fetch("/api/x").then(r => r.json());

// ✅ Usar entity hook
import { useX } from "@/entities/x";
const { data } = useX();
```

**Linter:** dep-cruiser `no-rogue-fetch` + grep.

### §2.4 — `useState` para server data (HIGH)

```typescript
// ❌ Re-fetcha en cada re-mount, no cache
const [orders, setOrders] = useState<Order[]>([]);
useEffect(() => { fetch("/api/orders").then(r => r.json()).then(setOrders); }, []);

// ✅ TanStack Query
const { data: orders } = useOrders();
```

**Linter:** convention + code review.

### §2.5 — `apiClient.get<T>(...)` sin Zod parse (HIGH)

```typescript
// ❌ compile-time T es documentación, no enforcement
const order = await apiClient.get<Order>("/api/orders/1");

// ✅ Zod at boundary
const raw = await apiClient.get<unknown>("/api/orders/1");
const order = orderSchema.parse(raw);
```

**Linter:** `test_zod_at_boundary` (AST scan).

### §2.6 — JSX en `.ts` extension (HIGH)

```typescript
// ❌ Component.ts con JSX adentro
// Vite/TS no parsea JSX en .ts files
export function Card() { return <div>...</div>; }

// ✅ Renombrar a Component.tsx
```

**Linter:** `test_jsx_uses_tsx_ext`.

### §2.7 — Cross-feature imports en `features/<a>` (MEDIUM)

```typescript
// ❌ features/orders/ui/X.tsx
import { ChatBubble } from "@/features/chats";   // cross-feature legacy
// (NOTA: dentro de plugins/<id>/frontend/features/ esto está PERMITIDO —
//  es solo legacy "features/" del root level)

// ✅ Promover ChatBubble a shared/ui/ si es genuinamente shared
import { ChatBubble } from "@/shared/ui";
```

**Linter:** dep-cruiser `legacy-features-isolated`.

### §2.8 — Tailwind tokens con naming `--color-text-*` (MEDIUM)

```css
/* ❌ src/index.css */
@theme {
  --color-text-primary: #000;       /* anti-pattern naming */
  --color-text-secondary: #666;
}

/* ✅ Usar fg / fg-muted */
@theme {
  --color-fg: #000;
  --color-fg-muted: #666;
}
```

**Linter:** `test_tailwind_token_naming`.

### §2.9 — Env vars hardcoded (MEDIUM)

```typescript
// ❌ feature/<x>/ui/Component.tsx
const apiUrl = "http://localhost:8000";          // hardcoded

// ✅ Via shared/config/env.ts
import { env } from "@/shared/config";
const apiUrl = env.apiUrl;
```

**Linter:** `test_env_centralization` + grep.

### §2.10 — Multiple QueryClients (LOW)

```typescript
// ❌ En cada test crea un QueryClient diferente sin retry: false
const qc = new QueryClient();  // ← retry defaults true → tests lentos

// ✅ Con retry: false
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
```

**Linter:** convention. Code review.

### §2.11 — Imports desde `@/features/*` (legacy)

`features/` (root level) es **legacy**. Antes de los plugins, las features
del dashboard vivían ahí. Post-PR2-PR7, todo fue migrado a
`plugins/<id>/frontend/features/`.

```typescript
// ❌ Usar legacy
import { ChatInbox } from "@/features/chat-inbox";

// ✅ Usar plugin
import { ChatInbox } from "@plugins/chats/frontend/features/chat-inbox";
// O mejor (si está en el barrel del plugin):
import { ChatsSection } from "@plugins/chats";
```

**Linter:** convention. `/src/features/` está deprecado y solo tiene
legacy slices que aún no se migraron (en teoría, todas ya están).

### §2.12 — Page mount sin code splitting

```typescript
// ❌ Direct import en Dashboard.tsx
import { ChatsSection } from "@plugins/chats";
// → ChatsSection se incluye en el bundle principal, no se carga lazy

// ✅ El registry usa lazy() — NO importes directo plugins en Dashboard
import { PLUGINS } from "@/app/plugin-registry.generated";
const ActivePage = PLUGINS.find(p => p.id === activeId)?.Page;
// ActivePage es ya LazyExoticComponent — code splitting funciona
```

**Linter:** convention.

### §2.13 — Component renderiza state global desde múltiples sources

```typescript
// ❌ Component que lee TanStack Query + useState + Context — sin estructura
function Component() {
  const { data } = useOrders();
  const [filter, setFilter] = useState("");
  const theme = useContext(ThemeContext);
  const { user } = useAuth();
  // ...
}

// ✅ Separar concerns:
// - server data: entity hooks
// - local state: feature/<x>/model/useY.ts
// - global state (raro): provider en app/providers/
```

**Linter:** convention. Code review.

### §2.14 — Manejo de error / loading sin UX consistente

```typescript
// ❌ Component sin loading / error state
function Orders() {
  const { data } = useOrders();
  return <div>{data.map(...)}</div>;        // crashea si data=undefined
}

// ✅ Patrón canónico
function Orders() {
  const { data, isLoading, error } = useOrders();
  if (isLoading) return <Spinner />;
  if (error) return <ErrorPanel error={error} />;
  if (!data || data.length === 0) return <EmptyState />;
  return <div>{data.map(...)}</div>;
}
```

**Linter:** convention.

### §2.15 — Backend-only plugin leaking into dashboard registry (HIGH)

Un plugin que NO declara bloque `frontend:` en `plugin.yaml` (porque expone
solo API REST y/o workers, o su UI vive en un container Vite separado) NO
debe aparecer en `src/app/plugin-registry.generated.ts`. Si lo hace, el
`Page: lazy(() => import("@plugins/<id>/frontend"))` rompe la pasada de
import-analysis de Vite con un error críptico:

```
[plugin:vite:import-analysis] Failed to resolve import "@plugins/<id>/frontend"
```

Caso paradigmático: `system_map` expone `/api/system-map/graph` y su UI vive
en `system_explorer/` (container Vite separado). Su manifest declara
explícitamente "NO frontend block".

```yaml
# ❌ Plugin backend-only con frontend block fake — el directorio no existe
id: system_map
frontend:
  entry: ./frontend     # ← no existe en disco, Vite muere en build

# ✅ Plugin backend-only correcto — sin frontend block
id: system_map
api:
  python_module: src.plugins.system_map.api
  prefix: /api/system-map
# (no frontend block — el sync script lo skipea correctamente)
```

**Contrato del sync script** (`scripts/plugins-sync.ts`):

1. Plugin sin `frontend:` → `logInfo("skip: backend-only")` + continue.
2. Plugin con `frontend:` pero entry inexistente → `logWarn` + skip.
3. Plugin con `frontend:` + entry válido → emit entry al registry.

**Linter:** `test_plugin_registry.arch.test.ts` (#19a + #19b):
- #19a: cada id en `PLUGINS[]` tiene `frontend:` block + entry en disco.
- #19b: cada plugin con `frontend:` válido aparece en `PLUGINS[]`.

---

## §3. Plugin isolation (regla adicional post-refactor)

### §3.1 Plugins prohibido importar `pages/` o `app/`

```typescript
// ❌ plugins/chats/frontend/ChatsSection.tsx
import { Dashboard } from "@/pages/Dashboard";   // upstream import
```

El plugin es agnóstico al shell. Si necesitás algo del shell, eso es un
prop bandejón (ver `sections/06-frontend-plugin.md §3`).

### §3.2 Plugins prohibido importar otros plugins

```typescript
// ❌ plugins/chats/frontend/ChatsSection.tsx
import { OrderCard } from "@plugins/orders/frontend/features/orders-board";

// ✅ Si necesitás funcionalidad cross-plugin, promote a entities/ o shared/
```

### §3.3 Cross-feature DENTRO del mismo plugin está OK (relajación)

```typescript
// ✅ plugins/chats/frontend/features/sales/ui/X.tsx
import { ChatBubble } from "../../shared/components/ChatBubble";
// O via paths:
import { ChatBubble } from "@plugins/chats/frontend/features/messages/components/ChatBubble";
```

Razón: el plugin es una unidad lógica. Sus features internas tienen
cross-dependencies naturales.

---

## §4. Tabla resumen — qué linter detecta qué

| Anti-pattern | Linter | Severidad |
|---|---|---|
| Cross-plugin import | dep-cruiser `plugins-no-cross-plugin` | CRITICAL |
| Deep import (`@plugins/X/ui/Y`) | dep-cruiser `no-deep-imports` | CRITICAL |
| `fetch()` directo en componente | dep-cruiser + grep | HIGH |
| `useState` para server data | convention | HIGH |
| `apiClient.get<T>()` sin Zod parse | `test_zod_at_boundary` (AST) | HIGH |
| JSX en `.ts` | `test_jsx_uses_tsx_ext` | HIGH |
| Cross-feature imports en `features/*` (legacy) | dep-cruiser `legacy-features-isolated` | MEDIUM |
| Tailwind `--color-text-*` | `test_tailwind_token_naming` | MEDIUM |
| Env var hardcoded | `test_env_centralization` | MEDIUM |
| Multiple QueryClients sin retry: false | convention | LOW |
| Legacy `features/` import | convention | LOW |
| Direct plugin import en Dashboard | convention | LOW |
| Component multi-state mess | convention | LOW |
| Falta loading/error state | convention | LOW |
| Backend-only plugin en registry | `test_plugin_registry.arch.test.ts` | HIGH |

---

## §5. Cheat sheet — checks rápidos antes de PR

```bash
cd frontend_dashboard

# 1. dep-cruiser
npm run test:arch

# 2. Type check
npx tsc -b

# 3. Build
npm run build

# 4. Grep checks rápidos
grep -rEn "fetch\(" src/plugins src/features 2>/dev/null | grep -v "// allowed:"
# → debe ser vacío

grep -rEn 'from "@plugins/[^"]+/(ui|model|features)/' src/plugins src/features 2>/dev/null
# → debe ser vacío (deep imports)

grep -rEn "process\.env\." src/plugins src/features 2>/dev/null | grep -v "src/shared/config"
# → debe ser vacío (env hardcoded)

grep -rEn "color-text-" src/ 2>/dev/null
# → debe ser vacío (Tailwind naming anti-pattern)
```

---

## §6. Cuándo cambiar las reglas (proceso ADR)

**Solo el operador**, NO el implementer. Igual que DEHA: ADR + PR separado
con label `architecture-change` + human review.

---

**Fin reference.**
