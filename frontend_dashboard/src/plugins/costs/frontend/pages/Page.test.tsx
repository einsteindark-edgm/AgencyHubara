/**
 * La pestaña completa contra el backend stubeado: arranca con las últimas dos
 * semanas, cada tarjeta carga sola y el total suma solo lo que llegó.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  CATALOG_FIXTURE,
  DEEPSEEK_REPORT_FIXTURE,
  NOT_CONNECTED_FIXTURE,
  WHATSAPP_REPORT_FIXTURE,
} from "@plugins/costs/frontend/entities/cost-report/fixtures";

import Page from "./Page";

const fetchMock = vi.fn();
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

function route(url: string) {
  if (url.includes("/api/costs/providers/deepseek")) return json(DEEPSEEK_REPORT_FIXTURE);
  if (url.includes("/api/costs/providers/temporal")) return json(NOT_CONNECTED_FIXTURE);
  if (url.includes("/api/costs/providers/whatsapp")) return json(WHATSAPP_REPORT_FIXTURE);
  if (url.includes("/api/costs/providers")) return json(CATALOG_FIXTURE);
  return json({ detail: "no" }, 404);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Page />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) => route(String(url)));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("Costs page", () => {
  it("starts with the last two weeks and adds the providers that answered", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$8,50"));
    expect(screen.getByLabelText("Desde")).toHaveValue("2026-10-22");
    expect(screen.getByText(/Parcial/)).toHaveTextContent("Temporal Cloud: sin conectar");
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/deepseek?start=2026-10-22&end=2026-11-04"))).toBe(true);
  });

  it("changing the range asks every provider again for the new dates", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("costs-total-usd")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Mes pasado" }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/whatsapp?start=2026-10-01&end=2026-10-31"))).toBe(true);
    });
  });

  it("a tab without the backend plugin explains it instead of staying blank", async () => {
    fetchMock.mockImplementation(() => json({ detail: "Not Found" }, 404));
    renderPage();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/ENABLED_PLUGINS/));
  });
});
