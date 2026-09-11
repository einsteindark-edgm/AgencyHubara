/**
 * Tabla de creativos de un segmento (2026-09-10): una fila por anuncio con
 * sus métricas Meta + chats, y el PESO de cada creativo sobre la campaña
 * completa (% de impresiones / clics / inversión / chats). Click en una fila
 * selecciona el anuncio (scope del canvas + inspector).
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsCreativesTable } from "./AdsCreativesTable";

function row(over: Partial<AdsCampaign> = {}): AdsCampaign {
  return {
    id: "AD_1", name: "Video velas", started: 10, dates: "—", sourceType: "ad",
    status: null, objective: null, placement: null, audience: null, daysRun: null,
    metaCampaignId: "CAMP_9", metaAdsetId: "ADSET_A", adSet: "Hombres 25-45",
    creativeTitle: "Chatea con nosotros", creativeThumbnailUrl: null, template: null,
    spend: 100000, impressions: 8000, reach: 6000, clicks: 200,
    conversationsStarted: 12,
    conversations: { no_reply: 2, nuevo: 2, activo: 1, calificado: 1, cotizado: 1, ganado: 3, perdido: 0 },
    revenue: null, avgTicket: null, llmCostUsd: null, llmTokens: null,
    avgEpisodeDurationMs: null, firstResp: null, tendency: null,
    capiLeadsSent: 0, capiPurchasesSent: 0, capiFailed: 0, capiSkipped: 0,
    ...over,
  };
}

const campaign = row({
  id: "CAMP_9", name: "Día del Padre", started: 40, spend: 400000,
  impressions: 16000, clicks: 400, metaAdsetId: null,
});

describe("AdsCreativesTable", () => {
  it("pinta una fila por anuncio con impresiones, clics, CTR y chats", () => {
    const { getByText, getAllByRole } = render(
      <AdsCreativesTable rows={[row(), row({ id: "AD_2", name: "Carrusel" })]} reference={campaign} selectedAdId={null} onSelect={() => {}} />,
    );
    expect(getByText("Video velas")).toBeTruthy();
    expect(getByText("Carrusel")).toBeTruthy();
    // CTR = 200 / 8000 = 2,50 %
    expect(getAllByRole("row").some((r) => /2[.,]50\s?%/.test(r.textContent ?? ""))).toBe(true);
  });

  it("calcula el peso del creativo sobre la campaña completa", () => {
    const { getAllByRole } = render(
      <AdsCreativesTable rows={[row()]} reference={campaign} selectedAdId={null} onSelect={() => {}} />,
    );
    const tr = getAllByRole("row")[1];
    const text = tr.textContent ?? "";
    // 8000/16000 impresiones, 200/400 clics, 100k/400k inversión, 10/40 chats
    expect(text).toMatch(/50[.,]0\s?%/);
    expect(text).toMatch(/25[.,]0\s?%/);
  });

  it("sin métricas Meta → celdas con dato pendiente, nunca 0 falso", () => {
    const { getAllByRole } = render(
      <AdsCreativesTable
        rows={[row({ impressions: null, clicks: null, spend: null, conversationsStarted: null })]}
        reference={campaign} selectedAdId={null} onSelect={() => {}}
      />,
    );
    const tr = getAllByRole("row")[1];
    const text = tr.textContent ?? "";
    // impresiones, clics, CTR, CPC, inversión, conv. Meta, costo/chat y los
    // tres pesos Meta → 10 celdas pendientes; el peso de chats sí se calcula.
    expect((text.match(/—/g) ?? []).length).toBeGreaterThanOrEqual(10);
    expect(text).toMatch(/100[.,]0\s?%/); // chats: 10 de 10 de la campaña
    expect(text).not.toMatch(/0[.,]00\s?%/);
  });

  it("click en una fila selecciona el anuncio; click de nuevo lo deselecciona", () => {
    const onSelect = vi.fn();
    const { getByText, rerender } = render(
      <AdsCreativesTable rows={[row()]} reference={campaign} selectedAdId={null} onSelect={onSelect} />,
    );
    fireEvent.click(getByText("Video velas"));
    expect(onSelect).toHaveBeenCalledWith("AD_1");
    rerender(
      <AdsCreativesTable rows={[row()]} reference={campaign} selectedAdId="AD_1" onSelect={onSelect} />,
    );
    fireEvent.click(getByText("Video velas"));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("sin anuncios → empty-state honesto", () => {
    const { getByText } = render(
      <AdsCreativesTable rows={[]} reference={campaign} selectedAdId={null} onSelect={() => {}} />,
    );
    expect(getByText(/sin anuncios/i)).toBeTruthy();
  });
});
