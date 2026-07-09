/**
 * Los −N rojos del embudo (2026-07-09): son la CAÍDA entre etapas (contactos
 * que no pasan a la siguiente) — dato real derivado, no quemado. El operador
 * no sabía qué eran: ahora llevan la palabra "caen" y una leyenda en el header.
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { AdsFunnel } from "./AdsFunnel";
import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

const campaign = {
  id: "AD_1",
  name: "Día del padre",
  started: 20,
  conversations: {
    no_reply: 1, nuevo: 2, activo: 2, calificado: 1, cotizado: 1, ganado: 12, perdido: 1,
  },
  impressions: 45000,
  clicks: 571,
  spend: 896823,
} as unknown as AdsCampaign;

describe("AdsFunnel — leyenda de las caídas", () => {
  it("explica qué son los −N rojos (caída entre etapas)", () => {
    const { getByText, getAllByText } = render(<AdsFunnel campaign={campaign} />);
    // leyenda visible en el header
    expect(getByText(/caen entre etapas/i)).toBeTruthy();
    // cada chip rojo lleva la palabra "caen" (no un número mudo)
    expect(getAllByText(/caen/i).length).toBeGreaterThan(1);
  });
});
