# Example — Plugin frontend-only (template A)

> **Plugins reales del repo que siguen este template:** `orders`, `eta`,
> `agents_admin`.
>
> **Use cuándo:** tu plugin solo tiene UI; consume datos via `entities/`
> shared o via endpoints de otro plugin.

---

## §1. Archivos típicos

```
frontend_dashboard/src/plugins/orders/
├── plugin.yaml                                # manifest mínimo
└── frontend/
    ├── index.ts                               # barrel
    ├── OrdersSection.tsx                      # Page root
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

---

## §2. Manifest (`plugin.yaml`)

```yaml
# frontend_dashboard/src/plugins/orders/plugin.yaml (real)
id: orders
version: 0.1.0
display_name: Orders
description: Tablero kanban de órdenes (estados + filtros + inspector). Plugin frontend-only por ahora — los datos vienen de `entities/order` (shared).

depends_on: []

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
export { default, OrdersSection } from "./OrdersSection";
export type { OrdersSectionProps } from "./OrdersSection";
```

---

## §4. Section component (`OrdersSection.tsx`)

```typescript
// canonical — plugins/orders/frontend/OrdersSection.tsx
import { useState } from "react";
import { OrdersBoard } from "./features/orders-board";
import { OrdersFilters } from "./features/orders-filters";
import { OrdersInspector } from "./features/orders-inspector";

export interface OrdersSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function OrdersSection({ showSidebar, showInspector }: OrdersSectionProps) {
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
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

- Props bandejón (`showSidebar` + `showInspector`) — el shell las pasa,
  el plugin las honora.
- Cross-feature import dentro del plugin OK (`OrdersFilters` y
  `OrdersBoard` se referencian via barrels).
- Estado local del plugin (selected + filter) vive en la Section. Si crece,
  considerar `features/orders-board/model/useOrdersState.ts`.

---

## §5. Una feature interna (`features/orders-board/`)

```typescript
// canonical — features/orders-board/ui/OrdersBoard.tsx
import { useOrders } from "@/entities/order";           // OK: entity shared
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
import { OrdersSection } from "./OrdersSection";

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => [{ id: "1", customer: "Acme", status: "pending" }],
  }));
});

afterEach(() => vi.restoreAllMocks());

test("renders board when showSidebar=false, showInspector=false", () => {
  render(wrap(<OrdersSection showSidebar={false} showInspector={false} />));
  expect(screen.getByRole("main")).toBeInTheDocument();
});

test("hides sidebar when showSidebar=false", () => {
  render(wrap(<OrdersSection showSidebar={false} showInspector />));
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

- `api:` block en el manifest — el plugin no tiene endpoints.
- `agent:` block — el plugin no tiene workers Temporal.
- `hubara_agency/src/plugins/orders/api/` — no existe.
- `hubara_agency/src/plugins/orders/agent/` — no existe.
- `hubara_agency/src/plugins/orders/workers/` — no existe.
- `hubara_agency/k8s/aws-produccion/worker-orders-*.yaml` — no existe.

Si tu plugin va a crecer (e.g. sumarle worker), ver `examples/plugin-with-worker.md`.

---

## §9. Pros y limitaciones del template A

| Pro | Limitación |
|---|---|
| Simple — solo frontend | No tiene lógica propia backend |
| 0 conflictos cross-plugin | Depende 100% de `entities/` shared y APIs de otros plugins |
| Setup rápido (~30 min) | Si el dominio crece, hay que promover a B o C |
| Tests rápidos (solo Vitest + Playwright) | Sin functional tests Python |

---

## §10. Cuándo NO usar template A

| Caso | Template recomendado |
|---|---|
| Necesitás endpoint propio | B (frontend + API) |
| Necesitás workflow Temporal | C o D |
| Necesitás integración con LLM | C o D |
| El dominio tiene state propio cross-plugin | Promover a `entities/` shared antes de meter en plugin |

---

**Fin example.**
