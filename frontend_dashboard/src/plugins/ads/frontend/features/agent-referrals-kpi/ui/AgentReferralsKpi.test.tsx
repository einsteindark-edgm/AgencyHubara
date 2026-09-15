/**
 * KPI "Desde agentes de IA": cuántas conversaciones de WhatsApp mandó ChatGPT,
 * Gemini… en la ventana del header, y cuántas terminaron en pedido. Es de TODA
 * la tienda (no de la campaña seleccionada) — el título lo dice.
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const mockQuery = vi.hoisted(() => ({ current: {} as object }));

vi.mock("@plugins/ads/frontend/entities/ads-campaign", () => ({
  useAgentReferrals: () => mockQuery.current,
}));

import { AgentReferralsKpi } from "./AgentReferralsKpi";

const params = { days: 30, from: null, to: null };

const data = {
  total: 4,
  withOrder: 1,
  bySource: [
    { source: "chatgpt", label: "ChatGPT", count: 3 },
    { source: "gemini", label: "Gemini", count: 1 },
    { source: "perplexity", label: "Perplexity", count: 0 },
  ],
};

describe("AgentReferralsKpi", () => {
  it("muestra el total, los pedidos y la conversión", () => {
    mockQuery.current = { data, isLoading: false, isError: false };
    const { getByText } = render(<AgentReferralsKpi params={params} />);

    expect(getByText(/desde agentes de ia/i)).toBeTruthy();
    expect(getByText("4")).toBeTruthy();
    expect(getByText("1 · 25%")).toBeTruthy();
  });

  it("desglosa por agente solo los que trajeron conversaciones", () => {
    mockQuery.current = { data, isLoading: false, isError: false };
    const { getByText, queryByText } = render(<AgentReferralsKpi params={params} />);

    expect(getByText("ChatGPT")).toBeTruthy();
    expect(getByText("Gemini")).toBeTruthy();
    expect(queryByText("Perplexity")).toBeNull();
  });

  it("sin referidos explica qué se está midiendo en vez de un cero mudo", () => {
    mockQuery.current = {
      data: { total: 0, withOrder: 0, bySource: [] },
      isLoading: false,
      isError: false,
    };
    const { getByText } = render(<AgentReferralsKpi params={params} />);

    expect(getByText(/todavía no llegó ninguna conversación/i)).toBeTruthy();
  });

  it("si el endpoint falla no rompe la vista de campañas", () => {
    mockQuery.current = { data: undefined, isLoading: false, isError: true };
    const { container } = render(<AgentReferralsKpi params={params} />);

    expect(container.innerHTML).toBe("");
  });
});
