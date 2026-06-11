import { describe, expect, it } from "vitest";
import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";
import { groupTrackedOrders } from "./groupTrackedOrders";

function order(over: Partial<TrackedOrder> = {}): TrackedOrder {
  return {
    id: "#1",
    customer: "Cliente WhatsApp",
    short: "CW",
    color: "a",
    phone: "573001112233",
    city: "",
    current: "shipping",
    channel: "WhatsApp",
    needs: false,
    payType: "confirmed",
    total: 10000,
    messagesUnread: 0,
    events: [],
    ...over,
  };
}

describe("groupTrackedOrders", () => {
  it("agrupa por teléfono aunque el nombre sea genérico e idéntico", () => {
    const groups = groupTrackedOrders([
      order({ id: "#6", phone: "573125671604" }),
      order({ id: "#8", phone: "573009998877" }),
      order({ id: "#5", phone: "573125671604" }),
    ]);
    expect(groups.map((g) => [g.key, g.orders.map((o) => o.id)])).toEqual([
      ["573125671604", ["#6", "#5"]],
      ["573009998877", ["#8"]],
    ]);
  });

  it("NO mezcla pedidos sin teléfono: cada uno es su propio grupo", () => {
    const groups = groupTrackedOrders([
      order({ id: "#2", phone: "" }),
      order({ id: "#3", phone: "" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.key)).toEqual(["id:#2", "id:#3"]);
  });

  it("agrega atención y total COD en calle del grupo (predicado del banner)", () => {
    const groups = groupTrackedOrders([
      order({ id: "#6", payType: "cod", current: "shipping", total: 97000 }),
      order({ id: "#8", payType: "cod", current: "ready", total: 61000, needs: true }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].needs).toBe(true);
    // Solo el #6 está en la calle — el #8 (ready) todavía no se cobra.
    expect(groups[0].codPending).toBe(97000);
  });

  it("preserva el orden del backend (primera aparición)", () => {
    const groups = groupTrackedOrders([
      order({ id: "#9", phone: "B" }),
      order({ id: "#1", phone: "A" }),
      order({ id: "#7", phone: "B" }),
    ]);
    expect(groups.map((g) => g.key)).toEqual(["B", "A"]);
  });
});
