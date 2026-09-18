/**
 * Buscador de la vista Órdenes.
 *
 * Bug reportado (2026-09-18): "Buscar # orden o cliente…" era un `<input>` sin
 * estado — escribir no filtraba el tablero. Contrato: lo escrito acota el
 * tablero (y el contador del header) a las órdenes que coinciden por número,
 * cliente o teléfono, y se combina con las vistas y la modalidad de pago.
 *
 * Se testea la composición real sidebar → `useOrderFilters` → tablero. El
 * tablero, el header y el inspector se stubean: acá importa QUÉ órdenes les
 * llegan, no cómo las pintan.
 */
import { describe, expect, it, vi } from "vitest";
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
  OrdersHeader: ({ filteredCount }: { filteredCount: number }) => (
    <output aria-label="Órdenes en vista">{filteredCount}</output>
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
    ...overrides,
  };
}

const ORDERS: Order[] = [
  order({ id: "#1247", customer: "María Gómez", phone: "+57 300 123 4567", payType: "cod" }),
  order({ id: "#1248", customer: "Juan Pérez", phone: "+57 300 111 2233", payType: "confirmed" }),
  order({ id: "#1249", customer: "María Gómez", phone: "+57 300 123 4567", payType: "confirmed" }),
];

function renderSection() {
  orders.current = ORDERS;
  render(<OrdersSection />);
  return userEvent.setup();
}

const searchBox = () => screen.getByPlaceholderText("Buscar # orden o cliente…");

/** Ids de las órdenes que le llegan al tablero. */
function boardIds(): string[] {
  const board = screen.getByRole("list", { name: "Tablero" });
  return within(board)
    .queryAllByRole("listitem")
    .map((li) => li.textContent ?? "");
}

/** Contador que acompaña a una fila de la sidebar. */
function countOf(label: string): number {
  const row = screen.getByText(label).closest(".of-row");
  return Number(row?.querySelector(".ofn")?.textContent);
}

describe("buscador de Órdenes", () => {
  it("un # de orden deja solo esa orden en el tablero y en el contador", async () => {
    const user = renderSection();
    await user.type(searchBox(), "#1248");

    expect(boardIds()).toEqual(["#1248"]);
    expect(screen.getByLabelText("Órdenes en vista")).toHaveTextContent("1");
  });

  it("busca por cliente sin importar tildes", async () => {
    const user = renderSection();
    await user.type(searchBox(), "maria");

    expect(boardIds()).toEqual(["#1247", "#1249"]);
  });

  it("busca por teléfono escrito sin espacios ni prefijo", async () => {
    const user = renderSection();
    await user.type(searchBox(), "3001112233");

    expect(boardIds()).toEqual(["#1248"]);
  });

  it("se combina con la modalidad de pago", async () => {
    const user = renderSection();
    await user.type(searchBox(), "maria");
    await user.click(screen.getByText("Anticipado"));

    expect(boardIds()).toEqual(["#1249"]);
  });

  it("los contadores de la sidebar cuentan solo lo que coincide", async () => {
    const user = renderSection();
    await user.type(searchBox(), "maria");

    expect(countOf("Todas")).toBe(2);
    expect(countOf("Anticipado")).toBe(1);
    expect(countOf("Contra entrega")).toBe(1);
  });
});
