/**
 * Vistas por fecha de Órdenes ("Para hoy" / "Mañana" / "Esta semana") en el
 * borde de las 19:00 hora Colombia.
 *
 * #259 pasó los CONTADORES de la sidebar al día colombiano, pero el FILTRO que
 * llena el tablero (`useOrderFilters`) siguió cortando en UTC. Desde las 19:00
 * locales (00:00Z) el tablero y el contador de la misma vista decían cosas
 * distintas: "Para hoy" mostraba las entregas de mañana, "Mañana" las de pasado
 * mañana y "Esta semana" perdía las de hoy.
 *
 * Se testea la composición real sidebar → `useOrderFilters` → tablero con el
 * reloj congelado a las 20:00 del 10 de septiembre en Bogotá (01:00Z del 11).
 * El tablero, el header y el inspector se stubean: acá importa QUÉ órdenes les
 * llegan, no cómo las pintan.
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
      // SIEMPRE el mismo array entre renders, como TanStack en producción
      // mientras no haya refetch. Los casos de medianoche dependen de esto: con
      // un array nuevo por render (`[...orders.current]`) el memo se recalcularía
      // siempre y dejarían de probar que el día tiene que estar en las deps.
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
  OrdersHeader: () => null,
}));

vi.mock("@plugins/orders/frontend/features/orders-inspector", () => ({
  OrdersInspector: () => null,
}));

vi.mock("@plugins/orders/frontend/features/orders-vault-reconciliation", () => ({
  VaultOrdersBanner: () => null,
}));

import { OrdersSection } from "./OrdersSection";

const VEINTE_HORAS_BOGOTA = new Date("2026-09-11T01:00:00.000Z");

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

// Sin canceladas a propósito: el contador las excluye y el tablero las muestra
// en su columna, así que con una cancelada los dos no tendrían por qué coincidir.
const ORDERS: Order[] = [
  order({ id: "hoy-1", dueIso: "2026-09-10" }),
  order({ id: "hoy-2", dueIso: "2026-09-10" }),
  order({ id: "manana", dueIso: "2026-09-11" }),
  order({ id: "pasado-manana", dueIso: "2026-09-12" }),
  // Último día de "Esta semana" (hoy + 6) y el primero que queda afuera.
  order({ id: "dia-16", dueIso: "2026-09-16" }),
  order({ id: "dia-17", dueIso: "2026-09-17" }),
  order({ id: "sin-agendar", dueIso: "" }),
];

function renderSection() {
  orders.current = ORDERS;
  const { rerender } = render(<OrdersSection />);
  return { user: userEvent.setup(), rerender: () => rerender(<OrdersSection />) };
}

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

beforeEach(() => {
  // Solo el reloj de calendario: los timers siguen reales para que userEvent
  // no se quede esperando un setTimeout que nadie avanza.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(VEINTE_HORAS_BOGOTA);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("vistas por fecha de Órdenes a las 20:00 hora Colombia", () => {
  it("'Para hoy' deja en el tablero las entregas de HOY en Colombia, no las de mañana", async () => {
    const { user } = renderSection();
    await user.click(screen.getByText("Para hoy"));

    expect(boardIds()).toEqual(["hoy-1", "hoy-2"]);
  });

  it("'Mañana' deja las del día siguiente colombiano, no las de pasado mañana", async () => {
    const { user } = renderSection();
    await user.click(screen.getByText("Mañana"));

    expect(boardIds()).toEqual(["manana"]);
  });

  it("'Esta semana' arranca HOY y cubre siete días", async () => {
    // 10 al 16 de septiembre. Con el corte UTC la ventana era 11–17: perdía
    // las dos de hoy y metía la del 17.
    const { user } = renderSection();
    await user.click(screen.getByText("Esta semana"));

    expect(boardIds()).toEqual(["hoy-1", "hoy-2", "manana", "pasado-manana", "dia-16"]);
  });

  it("el tablero muestra lo mismo que cuenta la sidebar en cada vista de fecha", async () => {
    const { user } = renderSection();
    for (const label of ["Para hoy", "Mañana", "Esta semana"]) {
      await user.click(screen.getByText(label));

      expect(boardIds(), label).toHaveLength(countOf(label));
    }
  });
});

describe("medianoche en Colombia con el dashboard abierto", () => {
  // Las TRES vistas, no solo "Para hoy": cada una deriva del reloj por su lado
  // (`today`, `tomorrow`, el Set de la semana) y cualquiera puede quedar
  // congelada sin que las otras lo noten. Sacar el Set a un `useMemo(…, [])`,
  // por ejemplo, pasa el linter y deja "Esta semana" en el día en que se abrió
  // la página.
  it.each([
    { vista: "Para hoy", antes: ["hoy-1", "hoy-2"], despues: ["manana"] },
    { vista: "Mañana", antes: ["manana"], despues: ["pasado-manana"] },
    {
      vista: "Esta semana",
      antes: ["hoy-1", "hoy-2", "manana", "pasado-manana", "dia-16"],
      despues: ["manana", "pasado-manana", "dia-16", "dia-17"],
    },
  ])(
    // vitest ya entrecomilla el valor interpolado: el título sale 'Para hoy' pasa…
    "$vista pasa al día nuevo en el siguiente render, igual que su contador",
    async ({ vista, antes, despues }) => {
      vi.setSystemTime(new Date("2026-09-11T04:30:00.000Z")); // 23:30 del 10 en Bogotá
      const { user, rerender } = renderSection();
      await user.click(screen.getByText(vista));
      expect(boardIds()).toEqual(antes);

      // Mismas órdenes, misma vista: el re-render lo dispara algo ajeno (un
      // evento del stream, elegir otra orden). Si el día quedara congelado en un
      // memo, el tablero seguiría en el 10 mientras el contador ya cuenta el 11.
      vi.setSystemTime(new Date("2026-09-11T05:30:00.000Z")); // 00:30 del 11 en Bogotá
      rerender();

      expect(boardIds()).toEqual(despues);
      expect(countOf(vista)).toBe(despues.length);
    },
  );
});
