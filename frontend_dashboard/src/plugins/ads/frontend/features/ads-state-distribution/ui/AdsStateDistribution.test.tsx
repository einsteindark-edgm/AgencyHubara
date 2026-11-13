/**
 * Leyenda de estados — auditoría 2026-11-10: «Ganado · Ingresos» multiplicaba
 * los ganados por el ticket promedio ($400K con 4 pedidos y 1 pagado de
 * $100K) y contradecía al KPI «Ingresos atribuidos». Ahora es el ingreso real.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsStateDistribution } from "./AdsStateDistribution";

const CAMPAIGN = {
  id: "AD_1",
  name: "Día del padre",
  started: 10,
  conversations: {
    no_reply: 2, nuevo: 0, activo: 1, calificado: 1, cotizado: 1, ganado: 4, perdido: 1,
  },
  revenue: 100_000,
  avgTicket: 100_000,
  paidSales: 1,
} as AdsCampaign;

function legend(container: HTMLElement, label: string): HTMLElement {
  const el = [...container.querySelectorAll(".state-leg")].find(
    (l) => l.querySelector(".sl-lbl")?.textContent === label,
  );
  if (!el) throw new Error(`estado «${label}» no está`);
  return el as HTMLElement;
}

describe("AdsStateDistribution — ingresos de los ganados", () => {
  it("«Ganado» muestra el ingreso real, no ganados × ticket", () => {
    const { container } = render(<AdsStateDistribution campaign={CAMPAIGN} />);

    expect(legend(container, "Ganado").querySelector(".sl-val")?.textContent).toBe(
      "Ingresos $100K",
    );
  });
});
