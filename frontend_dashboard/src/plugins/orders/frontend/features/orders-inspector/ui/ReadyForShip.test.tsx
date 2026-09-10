/**
 * El formulario de agendar entrega, en el borde de las 19:00 hora Colombia.
 *
 * Con el reloj congelado a las 20:00 del 10 de septiembre en Bogotá (01:00Z
 * del 11), el día UTC ya es el 11. Con el corte viejo eso tenía dos efectos
 * sobre este formulario:
 *   - `min` saltaba a mañana → el operador NO podía agendar para el día en
 *     curso a partir de las 7 de la tarde.
 *   - el default "mañana" caía en +2 días.
 *
 * Decidido con el operador (2026-09-10): se puede agendar para hoy hasta la
 * medianoche colombiana.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";

// La mutation real necesita QueryClient + apiClient; acá sólo se verifican los
// valores de fecha que el formulario propone.
vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useScheduleOrder: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    data: undefined,
  }),
}));

import { ReadyForShip } from "./ReadyForShip";

const VEINTE_HORAS_BOGOTA = new Date("2026-09-11T01:00:00.000Z");
const HOY_COLOMBIA = "2026-09-10";
const MANANA_COLOMBIA = "2026-09-11";

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "order_01",
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "+57 300 111 2233",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "ready",
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

function dateInput(): HTMLInputElement {
  return screen.getByLabelText(/fecha/i) as HTMLInputElement;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(VEINTE_HORAS_BOGOTA);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("fechas propuestas a las 20:00 hora Colombia", () => {
  it("todavía se puede agendar para HOY (min es el día colombiano)", () => {
    render(<ReadyForShip order={order()} />);
    expect(dateInput()).toHaveAttribute("min", HOY_COLOMBIA);
  });

  it("el default es mañana en Colombia, no +2 días", () => {
    render(<ReadyForShip order={order()} />);
    expect(dateInput().value).toBe(MANANA_COLOMBIA);
  });

  it("si la orden ya tiene fecha, esa gana sobre el default", () => {
    render(<ReadyForShip order={order({ dueIso: "2026-09-20" })} />);
    expect(dateInput().value).toBe("2026-09-20");
  });
});
