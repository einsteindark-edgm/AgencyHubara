/**
 * Aviso "valores de pedidos sin actualizar" — el revenue de Ads sale de Orders
 * (OrderFacts); si Orders/Medusa no respondió, el backend manda el último valor
 * conocido y `orders_stale=true`: el operador tiene que saberlo.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mock = vi.hoisted(() => ({ stale: false }));
vi.mock("@plugins/ads/frontend/entities/ads-campaign", () => ({
  useAdsOrdersStale: () => mock.stale,
}));

import { OrdersStaleNotice } from "./OrdersStaleNotice";

const params = { days: 30, from: null, to: null };

describe("OrdersStaleNotice", () => {
  it("no muestra nada con datos al día", () => {
    mock.stale = false;
    const { container } = render(<OrdersStaleNotice params={params} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("avisa cuando los valores de pedidos no están actualizados", () => {
    mock.stale = true;
    render(<OrdersStaleNotice params={params} />);
    expect(screen.getByRole("status")).toHaveTextContent(/sin actualizar/i);
  });
});
