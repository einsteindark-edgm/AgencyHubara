import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EtaCards } from "./EtaCards";
import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";

function makeOrder(over: Partial<TrackedOrder> = {}): TrackedOrder {
  return {
    id: "#5",
    customer: "Cliente WhatsApp",
    short: "CW",
    color: "a",
    phone: "573125671604",
    city: "Bogotá",
    current: "preparing",
    channel: "WhatsApp",
    needs: false,
    payType: "confirmed",
    total: 100000,
    messagesUnread: 0,
    events: [],
    ...over,
  };
}

describe("EtaCards", () => {
  it("renders an order with an EMPTY timeline without crashing", () => {
    // Regresión: los pedidos reales arrancan con `events: []` (el agente aún no
    // notificó). Antes la tarjeta hacía `events[events.length - 1]` →
    // `lastEvent` undefined → `lastEvent.date` TypeError → toda la sección
    // ETA se caía aunque el backend devolviera los pedidos (gotcha #1).
    const { getByText } = render(
      <EtaCards
        orders={[makeOrder({ id: "#5", events: [] })]}
        filterLabel="Todos los pedidos rastreados"
        selectedId={null}
        onSelect={() => {}}
      />,
    );
    expect(getByText("#5")).toBeTruthy();
    expect(getByText(/Aún sin notificaciones/i)).toBeTruthy();
  });

  it("agrupa por cliente: header con teléfono, conteo y COD a cobrar hoy", () => {
    const { getByText } = render(
      <EtaCards
        orders={[
          makeOrder({ id: "#6", payType: "cod", current: "shipping", total: 97000 }),
          makeOrder({ id: "#8", payType: "cod", current: "ready", total: 61000 }),
          makeOrder({ id: "#9", phone: "573009998877", customer: "Otra Persona" }),
        ]}
        filterLabel="Todos"
        selectedId={null}
        onSelect={() => {}}
      />,
    );
    expect(getByText("+57 312 567 1604")).toBeTruthy();
    expect(getByText("2 pedidos")).toBeTruthy(); // grupo del 573125671604
    // A cobrar hoy del grupo = SOLO el #6 (en la calle); el #8 (ready) no.
    expect(getByText("$ 97.000")).toBeTruthy();
    expect(getByText("Otra Persona")).toBeTruthy();
    expect(getByText(/2 cliente/)).toBeTruthy(); // subtítulo del canvas
  });

  it("renders the last agent message when the timeline has events", () => {
    const order = makeOrder({
      id: "#4",
      current: "shipping",
      events: [
        { stage: "preparing", time: "10:00", date: "hoy", agentMsg: "Entró en preparación", reply: null, flagged: false },
        { stage: "shipping", time: "12:30", date: "hoy", agentMsg: "Tu pedido va en camino", reply: null, flagged: false },
      ],
    });
    const { getByText } = render(
      <EtaCards orders={[order]} filterLabel="Todos" selectedId={null} onSelect={() => {}} />,
    );
    expect(getByText(/Tu pedido va en camino/)).toBeTruthy();
  });
});
