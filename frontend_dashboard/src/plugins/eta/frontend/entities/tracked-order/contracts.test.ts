/**
 * Tests del contrato Zod de `/api/eta/tracked-orders` — la primera línea de
 * defensa contra contract drift (mismo espíritu que `order/api.test.ts`).
 *
 * Regresión L-10: el backend empezó a emitir eventos de timeline con stage
 * `cancelled` (el agente notifica cancelaciones) y el enum del frontend no lo
 * aceptaba → `.parse()` tumbaba la respuesta ENTERA y la sección ETA quedaba
 * vacía en silencio. El timeline acepta `cancelled`; el stage ACTUAL del
 * pedido sigue restringido al tablero (cancelled nunca se lista — filtra el
 * backend).
 */

import { describe, expect, it } from "vitest";
import {
  trackedEventStageSchema,
  trackedOrdersListResponseSchema,
} from "./contracts";

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: "#6",
    customer: "Cliente WhatsApp",
    short: "CW",
    color: "a",
    city: "",
    current: "shipping",
    channel: "WhatsApp",
    needs: false,
    payType: "cod",
    total: 97000,
    messagesUnread: 0,
    events: [],
    ...overrides,
  };
}

function event(overrides: Record<string, unknown> = {}) {
  return {
    stage: "shipping",
    time: "18:32",
    date: "hoy",
    note: null,
    agentMsg: "Tu pedido #6 ya va en camino 🚚",
    reply: null,
    flagged: false,
    flag: null,
    ...overrides,
  };
}

describe("trackedOrdersListResponseSchema", () => {
  it("acepta una respuesta real con timeline", () => {
    const parsed = trackedOrdersListResponseSchema.parse({
      orders: [order({ events: [event()] })],
      count: 1,
    });
    expect(parsed.orders).toHaveLength(1);
    expect(parsed.orders[0].events[0].stage).toBe("shipping");
  });

  it("acepta eventos `cancelled` en el timeline (regresión L-10)", () => {
    const parsed = trackedOrdersListResponseSchema.parse({
      orders: [
        order({
          events: [
            event({ stage: "cancelled", agentMsg: "Tu pedido #6 fue cancelado." }),
            event(),
          ],
        }),
      ],
      count: 1,
    });
    expect(parsed.orders[0].events.map((e) => e.stage)).toEqual([
      "cancelled",
      "shipping",
    ]);
  });

  it("rechaza `cancelled` como stage ACTUAL del pedido (fuera del tablero)", () => {
    expect(() =>
      trackedOrdersListResponseSchema.parse({
        orders: [order({ current: "cancelled" })],
        count: 1,
      }),
    ).toThrow();
  });

  it("rechaza stages desconocidos en eventos", () => {
    expect(() => trackedEventStageSchema.parse("teleported")).toThrow();
  });
});
