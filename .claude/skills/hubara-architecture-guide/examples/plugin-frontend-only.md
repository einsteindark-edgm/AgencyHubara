# Example — Plugin frontend-only (template A)

> **(post-refactor F1-F8)** Hoy NINGÚN plugin del repo queda en template A
> puro — `orders` (la base de este ejemplo), `eta` y `agents_admin` fueron
> promovidos (todos declaran `api:`; orders/eta además `agent:`). El
> frontend que se muestra acá sigue siendo el canónico.
>
> **Use cuándo:** tu plugin solo tiene UI estática/derivada. Si necesita
> datos de red, ya NO es template A puro: los sirve TU propia API
> (template B) y los consume una entity LOCAL
> (`frontend/entities/<x>/` → `/api/<tu-id>/*`). Llamar `/api/<otro>/`
> desde tu frontend está prohibido (gate P-9); el dato de otro plugin
> entra por cast declarado (`consumes:` — PLUGIN_CONTRACT.md §5.3).

---

## §1. Archivos típicos

```
frontend_dashboard/src/plugins/orders/
├── plugin.yaml                                # manifest mínimo
└── frontend/
    ├── index.ts                               # barrel
    ├── OrdersSection.tsx                      # Page root
    ├── entities/
    │   └── order/                             # entity LOCAL del plugin (post-F1-F8;
    │       ├── api.ts                         #   llama SOLO /api/orders/* — P-9)
    │       ├── contracts.ts
    │       ├── keys.ts
    │       ├── model.ts
    │       └── index.ts
    └── features/
        ├── orders-board/
        │   ├── index.ts
        │   └── ui/OrdersBoard.tsx             # kanban-style board
        ├── orders-filters/
        │   ├── index.ts
        │   └── ui/OrdersFilters.tsx
        └── orders-inspector/
            ├── index.ts
            └── ui/OrdersInspector.tsx         # detail panel right side

hubara_agency/src/plugins/orders/
└── __init__.py                                # anchor con docstring (no expone nada)
```

(El dir `entities/` solo aparece si el plugin tiene datos de red — y en
ese caso el plugin también necesita su `api:`, ver nota del header.)

---

## §2. Manifest (`plugin.yaml`)

```yaml
# frontend_dashboard/src/plugins/orders/plugin.yaml (histórico — el orders
# real hoy es v0.2.0 full-stack: sumó `api:` + `agent:`; este es el
# manifest mínimo de un template A)
id: orders
version: 0.1.0
display_name: Orders
description: Tablero kanban de órdenes (estados + filtros + inspector). Plugin frontend-only por ahora — los datos vendrán de su propia API + entity local cuando se promueva a B.

depends_on: []                    # deps DURAS — validadas al boot (P-6) si declarás alguna

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: orders, label: Orders, order: 2, icon: workflow }
    sidebar:
      - { route: /orders, label: Orders, icon: workflow }

# Sin api: ni agent: por ahora. Comentario en el manifest explica futuro:
# Cuando se necesite CRUD propio o integración con shipping providers, agregar:
#   api: { python_module: src.plugins.orders.api, prefix: /api/orders, ... }
#   agent: { workers: [{name: shipping_sync, module: ...}] }

wiring_intents:
  env_vars_required: []
```

---

## §3. Barrel del plugin (`frontend/index.ts`)

```typescript
// canonical — plugins/orders/frontend/index.ts
// El default es OBLIGATORIO: el registry lo verifica en compilación
// (assertPluginModule). Ya no hay Props que exportar (el Page no recibe props).
export { default, OrdersSection } from "./OrdersSection";
```

---

## §4. Section component (`OrdersSection.tsx`)

```typescript
// canonical — plugins/orders/frontend/OrdersSection.tsx
import { useState } from "react";
import { usePluginHost, useSelection } from "@/shared/lib";
import { OrdersBoard } from "./features/orders-board";
import { OrdersFilters } from "./features/orders-filters";
import { OrdersInspector } from "./features/orders-inspector";

// El Page NO recibe props (post-F7): chrome y selección llegan por el
// contexto PluginHost que el shell provee.
export function OrdersSection() {
  const { showSidebar, showInspector } = usePluginHost();
  // Selección persistente cross-sección — clave = el plugin id; el
  // segundo arg (opcional) es el fallback inicial.
  const [selectedOrderId, setSelectedOrderId] = useSelection("orders");
  const [filter, setFilter] = useState<string>("");

  return (
    <>
      {showSidebar && (
        <aside className="sidebar glass-panel">
          <OrdersFilters value={filter} onChange={setFilter} />
        </aside>
      )}
      <main className="main">
        <OrdersBoard
          filter={filter}
          selectedOrderId={selectedOrderId}
          onSelect={setSelectedOrderId}
        />
      </main>
      {showInspector && (
        <aside className="inspector glass-panel">
          {selectedOrderId ? (
            <OrdersInspector orderId={selectedOrderId} />
          ) : (
            <p>Selecciona una orden</p>
          )}
        </aside>
      )}
    </>
  );
}

export default OrdersSection;
```

**Notar:**

- Contrato PluginHost (`usePluginHost()` + `useSelection("orders")`) — el
  Page no recibe props; el "props bandejón" ya no existe (post-F7).
- Cross-feature import dentro del plugin OK (`OrdersFilters` y
  `OrdersBoard` se referencian via barrels, siempre por alias `@plugins/...`).
- La selección vive en el PluginHost (sobrevive cambios de sección); el
  estado puramente local (filter) sigue en `useState`. Si crece,
  considerar `features/orders-board/model/useOrdersState.ts`.

---

## §5. Una feature interna (`features/orders-board/`)

```typescript
// canonical — features/orders-board/ui/OrdersBoard.tsx
// Entity LOCAL del plugin (post-F1-F8) — alias completo, nunca "@/entities/":
import { useOrders } from "@plugins/orders/frontend/entities/order";
import { OrderCard } from "./OrderCard";

interface Props {
  filter: string;
  selectedOrderId: string | null;
  onSelect: (id: string) => void;
}

export function OrdersBoard({ filter, selectedOrderId, onSelect }: Props) {
  const { data: orders, isLoading, error } = useOrders();

  if (isLoading) return <div className="spinner">Loading orders…</div>;
  if (error) return <div className="error">Error: {error.message}</div>;
  if (!orders || orders.length === 0) return <div className="empty">No orders.</div>;

  const filtered = orders.filter(o =>
    !filter || o.customer.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="board">
      {["pending", "shipped", "delivered"].map(status => (
        <div key={status} className="board-column">
          <h3>{status}</h3>
          {filtered.filter(o => o.status === status).map(o => (
            <OrderCard
              key={o.id}
              order={o}
              selected={o.id === selectedOrderId}
              onClick={() => onSelect(o.id)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// features/orders-board/index.ts
export { OrdersBoard } from "./ui/OrdersBoard";
```

---

## §6. Tests del plugin

```typescript
// canonical — plugins/orders/frontend/OrdersSection.test.tsx
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PluginHostProvider, type PluginHostState } from "@/shared/lib";
import { OrdersSection } from "./OrdersSection";

// El Page no recibe props — el shell-state se inyecta vía PluginHostProvider.
function wrap(overrides: Partial<PluginHostState> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host: PluginHostState = {
    showSidebar: true,
    showInspector: true,
    selection: {},
    setSelection: vi.fn(),
    ...overrides,
  };
  return (
    <QueryClientProvider client={qc}>
      <PluginHostProvider value={host}>
        <OrdersSection />
      </PluginHostProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => [{ id: "1", customer: "Acme", status: "pending" }],
  }));
});

afterEach(() => vi.restoreAllMocks());

test("renders board when showSidebar=false, showInspector=false", () => {
  render(wrap({ showSidebar: false, showInspector: false }));
  expect(screen.getByRole("main")).toBeInTheDocument();
});

test("hides sidebar when showSidebar=false", () => {
  render(wrap({ showSidebar: false }));
  expect(screen.queryByRole("complementary", { name: /sidebar/i })).not.toBeInTheDocument();
});
```

---

## §7. Playwright E2E (mínimo)

```typescript
// frontend_dashboard/e2e/orders/board.spec.ts
import { expect, test } from "@playwright/test";

test.describe("orders", () => {
  test("operator can see orders board", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: "Orders" }).click();
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByText(/no orders|pending|shipped/i)).toBeVisible();
  });
});
```

---

## §8. Lo que NO está acá (porque no aplica al template A)

- `api:` block en el manifest — el plugin no tiene endpoints (y por eso
  tampoco entities con fetch: sin API propia no hay de dónde leer — P-9).
- `agent:` block — el plugin no tiene workers Temporal.
- `hubara_agency/src/plugins/<id>/api/` — no existe.
- `hubara_agency/src/plugins/<id>/agent/` — no existe.
- `hubara_agency/src/plugins/<id>/workers/` — no existe.
- `hubara_agency/k8s/aws-produccion/worker-<id>-*.yaml` — no existe.

(En el `orders` REAL de hoy todos esos paths SÍ existen — fue promovido a
full-stack. Acá describen el template A genérico.)

Si tu plugin va a crecer (e.g. sumarle worker), ver `examples/plugin-with-worker.md`.

---

## §9. Pros y limitaciones del template A

| Pro | Limitación |
|---|---|
| Simple — solo frontend | No tiene lógica propia backend |
| 0 conflictos cross-plugin | Sin API propia no puede mostrar datos de red (P-9 prohíbe `/api/<otro>/`; no hay entities shared) |
| Setup rápido (~30 min) | Si el dominio crece, hay que promover a B o C |
| Tests rápidos (solo Vitest + Playwright) | Sin functional tests Python |

---

## §10. Cuándo NO usar template A

| Caso | Template recomendado |
|---|---|
| Necesitás mostrar datos de red (propios o de otro plugin) | B (frontend + API) — entity local + tu API; si el dato es ajeno, cast declarado (`consumes:`) |
| Necesitás endpoint propio | B (frontend + API) |
| Necesitás workflow Temporal | C o D |
| Necesitás integración con LLM | C o D |
| Otro plugin necesita TUS datos | Publicar contrato versionado (`order@v1`-style); el consumidor declara `depends_on` + `consumes` — NUNCA entities compartidas |

---

**Fin example.**
