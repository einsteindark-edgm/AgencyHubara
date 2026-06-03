import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EtaCards } from "./EtaCards";
import type { TrackedOrder } from "@/entities/tracked-order";

function makeOrder(over: Partial<TrackedOrder> = {}): TrackedOrder {
  return {
    id: "#5",
    customer: "Cliente WhatsApp",
    short: "CW",
    color: "a",
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
