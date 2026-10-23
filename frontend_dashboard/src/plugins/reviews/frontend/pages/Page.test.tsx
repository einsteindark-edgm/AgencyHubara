/**
 * Page de Reseñas: compone el resumen (arriba) y la moderación (abajo). Los
 * Pages no reciben props; cada parte trae sus datos de `/api/reviews/*`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import {
  REVIEW_FIXTURE,
  SUMMARY_FIXTURE,
} from "@plugins/reviews/frontend/entities/review/fixtures";

import Page from "./Page";
import PluginEntry from "../index";

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = new URL(String(url));
    if (u.pathname === "/api/reviews/summary") return json(SUMMARY_FIXTURE);
    if (u.pathname === "/api/reviews/reviews")
      return json({ reviews: [REVIEW_FIXTURE], counts: { pending: 1, approved: 0, hidden: 0 } });
    return json({}, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
}

describe("Page de Reseñas", () => {
  it("el plugin exporta esta Page por defecto (el registry la monta)", () => {
    expect(PluginEntry).toBe(Page);
  });

  it("título, resumen y bandejas en una sola pantalla", async () => {
    renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "Reseñas" })).toBeInTheDocument();
    expect(await screen.findByText("4,6")).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Por revisar (1)" })).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Reseña del pedido #31" })).toBeInTheDocument();
  });

  it("mientras carga lo dice (no muestra una pantalla vacía)", () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    renderPage();

    expect(screen.getByText("Cargando el resumen…")).toBeInTheDocument();
    expect(screen.getByText("Cargando reseñas…")).toBeInTheDocument();
  });
});
