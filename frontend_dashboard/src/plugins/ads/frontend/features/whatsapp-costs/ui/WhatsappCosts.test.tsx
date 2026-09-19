/**
 * Panel "Costos de WhatsApp" de la campaña (2026-09-18).
 *
 * Pedido del operador: en el panel principal, el acumulado de costos de
 * WhatsApp de la campaña POR CATEGORÍA, separado con una línea divisoria
 * rotulada para leerlo de un vistazo y no confundirlo con el gasto del anuncio.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

import { WhatsappCosts } from "./WhatsappCosts";

function campaign(overrides: Partial<AdsCampaign>): AdsCampaign {
  return { id: "CAMP_9", name: "Día del Padre", started: 14, ...overrides } as AdsCampaign;
}

const WITH_COSTS = campaign({
  waCostUsdMicros: 22_900,
  waCostByCategory: {
    service: { count: 12, usdMicros: 9_600 },
    marketing: { count: 1, usdMicros: 12_500 },
    utility: { count: 1, usdMicros: 800 },
  },
  waMsgsPending: 0,
});

describe("WhatsappCosts", () => {
  it("separa la sección con un divisor rotulado 'Costos de WhatsApp'", () => {
    render(<WhatsappCosts campaign={WITH_COSTS} />);
    const divider = screen.getByRole("separator", { name: "Costos de WhatsApp" });
    expect(divider).toBeTruthy();
  });

  it("muestra el total en US$ (no se confunde con el gasto en COP del anuncio)", () => {
    render(<WhatsappCosts campaign={WITH_COSTS} />);
    const total = screen.getByTestId("wa-cost-total");
    expect(within(total).getByText("US$0.0229")).toBeTruthy();
    expect(within(total).getByText("14 mensajes")).toBeTruthy();
  });

  it("una tarjeta por categoría, con su monto y cuántos mensajes", () => {
    render(<WhatsappCosts campaign={WITH_COSTS} />);
    const marketing = screen.getByTestId("wa-cost-marketing");
    expect(within(marketing).getByText("Marketing")).toBeTruthy();
    expect(within(marketing).getByText("US$0.0125")).toBeTruthy();
    expect(within(marketing).getByText("1 mensaje")).toBeTruthy();
    const service = screen.getByTestId("wa-cost-service");
    expect(within(service).getByText("US$0.0096")).toBeTruthy();
    expect(within(service).getByText("12 mensajes")).toBeTruthy();
  });

  it("una categoría gratis se ve como 'Gratis', no como un costo vacío", () => {
    render(
      <WhatsappCosts
        campaign={campaign({
          waCostUsdMicros: 0,
          waCostByCategory: { service: { count: 7, usdMicros: 0 } },
        })}
      />,
    );
    expect(within(screen.getByTestId("wa-cost-service")).getByText("Gratis")).toBeTruthy();
  });

  it("avisa cuando hay mensajes cuyo precio aún no llegó (el total no es final)", () => {
    render(<WhatsappCosts campaign={{ ...WITH_COSTS, waMsgsPending: 3 }} />);
    expect(screen.getByText(/3 mensajes sin precio todavía/)).toBeTruthy();
  });

  it("sin dato de costo no pinta nada (null ≠ costó cero)", () => {
    const { container } = render(
      <WhatsappCosts campaign={campaign({ waCostUsdMicros: null, waCostByCategory: null })} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
