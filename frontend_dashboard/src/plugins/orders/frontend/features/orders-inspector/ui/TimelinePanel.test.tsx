/**
 * El paso de pago del timeline refleja el ÚLTIMO evento de pago: tras
 * "Reversar pago" ya no puede decir "Pago confirmado".
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Order, OrderDetail } from "@plugins/orders/frontend/entities/order";

import { TimelinePanel } from "./TimelinePanel";

const order = { id: "#32", status: "preparing" } as Order;

function detailWith(timeline: OrderDetail["timeline"]): OrderDetail {
  return { timeline } as OrderDetail;
}

const confirmed = { type: "payment_confirmed", label: "Pago confirmado", timestamp_ms: 1000, detail: null };
const reversed = { type: "payment_reversed", label: "Pago reversado", timestamp_ms: 2000, detail: "por edgm" };

describe("paso de pago del timeline", () => {
  it("confirmado → 'Pago confirmado'", () => {
    render(<TimelinePanel detail={detailWith([confirmed])} order={order} missing={new Set()} />);
    expect(screen.getByText("Pago confirmado")).toBeInTheDocument();
  });

  it("reversado después de confirmar → 'Pago reversado'", () => {
    render(<TimelinePanel detail={detailWith([confirmed, reversed])} order={order} missing={new Set()} />);
    expect(screen.queryByText("Pago confirmado")).toBeNull();
    expect(screen.getByText("Pago reversado")).toBeInTheDocument();
  });

  it("re-confirmado después de reversar → 'Pago confirmado'", () => {
    const again = { ...confirmed, timestamp_ms: 3000 };
    render(<TimelinePanel detail={detailWith([confirmed, reversed, again])} order={order} missing={new Set()} />);
    expect(screen.getByText("Pago confirmado")).toBeInTheDocument();
    expect(screen.queryByText("Pago reversado")).toBeNull();
  });
});
