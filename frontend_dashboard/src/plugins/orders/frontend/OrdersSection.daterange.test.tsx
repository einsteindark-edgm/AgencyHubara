/**
 * Filtro por fecha de Órdenes — el mismo calendario de la bandeja de Chats.
 *
 * Reporte del operador (2026-09-21): en Órdenes "no está funcionando el
 * filtro" y el tablero (KPIs) no se podía acotar a un período. Contrato:
 *   - La sidebar tiene el calendario (colapsado, "Todas las fechas").
 *   - Un día o rango acota el kanban, los contadores de la sidebar y los KPIs.
 *   - Cada orden cae en el calendario por su día de ENTREGA agendada; si no
 *     está agendada, por el día en que se creó (si no, un rango la escondería
 *     siempre y "Sin agendar" quedaría en cero con cualquier fecha).
 *   - Los KPIs NO se acotan por la vista ("Para hoy", "En camino"…): son el
 *     resumen del período, no de la columna que se está mirando.
 *
 * El tablero y el header se stubean: acá importa QUÉ órdenes les llegan.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Order } from "@plugins/orders/frontend/entities/order";

const orders = vi.hoisted(() => ({ current: [] as Order[] }));

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: false }),
  useSelection: () => [null, () => {}],
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOrders: () => ({
    data: {
      orders: orders.current,
      response: { catalog_available: true, error_detail: null },
    },
    isLoading: false,
  }),
  useVaultOrders: () => ({ data: undefined }),
  useOrdersEvents: () => {},
}));

vi.mock("@plugins/orders/frontend/features/orders-board", () => ({
  OrdersBoard: ({ orders }: { orders: Order[] }) => (
    <ul aria-label="Tablero">
      {orders.map((o) => (
        <li key={o.id}>{o.id}</li>
      ))}
    </ul>
  ),
  OrdersHeader: ({ orders, rangeLabel }: { orders: Order[]; rangeLabel: string }) => (
    <section aria-label="KPIs" data-range={rangeLabel}>
      {orders.map((o) => (
        <span key={o.id}>{o.id}</span>
      ))}
    </section>
  ),
}));

vi.mock("@plugins/orders/frontend/features/orders-inspector", () => ({
  OrdersInspector: () => null,
}));

vi.mock("@plugins/orders/frontend/features/orders-vault-reconciliation", () => ({
  VaultOrdersBanner: () => null,
}));

import { OrdersSection } from "./OrdersSection";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 89000,
    dueIso: "",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  };
}

// Hoy = 21 de septiembre en Colombia; "Ayer" = 20.
const ORDERS: Order[] = [
  order({ id: "entrega-ayer", dueIso: "2026-09-20", createdIso: "2026-09-18" }),
  order({ id: "creada-ayer-sin-agendar", dueIso: "", createdIso: "2026-09-20" }),
  order({ id: "entrega-ayer-anticipado", dueIso: "2026-09-20", payType: "confirmed" }),
  order({ id: "entrega-hoy", dueIso: "2026-09-21", createdIso: "2026-09-20" }),
  // Creada ayer pero con entrega agendada otro día: manda la entrega.
  order({ id: "creada-ayer-entrega-10", dueIso: "2026-09-10", createdIso: "2026-09-20" }),
];

function renderSection() {
  orders.current = ORDERS;
  render(<OrdersSection />);
  return userEvent.setup();
}

function idsIn(name: string, role: "list" | "region"): string[] {
  const el = screen.getByRole(role, { name });
  return Array.from(el.children).map((c) => c.textContent ?? "");
}
const boardIds = () => idsIn("Tablero", "list");
const kpiIds = () => idsIn("KPIs", "region");

function countOf(label: string): number {
  const row = screen.getByText(label).closest(".of-row");
  return Number(row?.querySelector(".ofn")?.textContent);
}

async function pickPreset(user: ReturnType<typeof userEvent.setup>, preset: string) {
  await user.click(screen.getByRole("button", { name: /todas las fechas|filtrar por fecha/i }));
  await user.click(screen.getByRole("button", { name: preset }));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-21T15:00:00.000Z")); // 10:00 Bogotá
});
afterEach(() => {
  vi.useRealTimers();
});

describe("calendario de Órdenes", () => {
  it("la sidebar trae el filtro por fecha, cerrado y sin rango", () => {
    renderSection();
    expect(screen.getByRole("button", { name: /filtrar por fecha/i })).toHaveTextContent(
      "Todas las fechas",
    );
    expect(boardIds()).toHaveLength(ORDERS.length);
  });

  it("'Ayer' deja en el tablero las órdenes de ayer: por entrega, o por creación si no están agendadas", async () => {
    const user = renderSection();
    await pickPreset(user, "Ayer");

    expect(boardIds()).toEqual([
      "entrega-ayer",
      "creada-ayer-sin-agendar",
      "entrega-ayer-anticipado",
    ]);
  });

  it("los contadores de la sidebar cuentan solo el período elegido", async () => {
    const user = renderSection();
    await pickPreset(user, "Ayer");

    expect(countOf("Todas")).toBe(3);
    expect(countOf("Sin agendar")).toBe(1);
    expect(countOf("Para hoy")).toBe(0);
  });

  it("los KPIs siguen el rango y la modalidad, pero no la vista", async () => {
    const user = renderSection();
    await pickPreset(user, "Ayer");
    await user.click(screen.getByText("Sin agendar"));
    await user.click(screen.getByText("Contra entrega"));

    // Tablero: vista + modalidad + rango.
    expect(boardIds()).toEqual(["creada-ayer-sin-agendar"]);
    // KPIs: rango + modalidad (la anticipada queda afuera), sin la vista.
    expect(kpiIds()).toEqual(["entrega-ayer", "creada-ayer-sin-agendar"]);
    expect(screen.getByRole("region", { name: "KPIs" })).toHaveAttribute("data-range", "Ayer");
  });

  it("'Limpiar' vuelve a mostrar todas las fechas", async () => {
    const user = renderSection();
    await pickPreset(user, "Ayer");
    await user.click(screen.getByRole("button", { name: "Limpiar" }));

    expect(boardIds()).toHaveLength(ORDERS.length);
    expect(within(screen.getByRole("region", { name: "KPIs" })).getAllByText(/./)).toHaveLength(
      ORDERS.length,
    );
  });
});
