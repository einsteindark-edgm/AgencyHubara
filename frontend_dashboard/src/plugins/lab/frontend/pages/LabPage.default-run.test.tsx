import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { labKeys } from "@plugins/lab/frontend/entities/lab-run";

import LabPage from "./LabPage";

/**
 * Qué corrida se ve al entrar y cuándo se relee la lista: la más nueva puede
 * estar corriendo o haber fallado sin publicar nada ("todavía no publicó…");
 * y al terminar la corrida en curso, su fase y su costo se ven sin esperar.
 */
const fetchMock = vi.fn();
let active: unknown = { active: null };

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

const RUNS = {
  runs: [
    { run_id: "run-20260924-100000-bbbb", phase: "simulating", arms: ["A1"], started_at_ms: 2 },
    { run_id: "run-20260923-100000-aaaa", phase: "done", arms: ["A1"], started_at_ms: 1 },
  ],
};

beforeEach(() => {
  active = { active: null };
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith("/api/lab/runs")) return json(RUNS);
    if (u.endsWith("/api/lab/runs/active")) return json(active);
    return json({ detail: "no" }, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPage(client: QueryClient) {
  render(
    <QueryClientProvider client={client}>
      <LabPage />
    </QueryClientProvider>,
  );
}

describe("LabPage", () => {
  it("abre en la última corrida terminada, no en la que sigue corriendo", async () => {
    renderPage(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    const select = await screen.findByRole("combobox", { name: "Corrida" });

    expect(select).toHaveValue("run-20260923-100000-aaaa");
  });

  it("cuando la corrida en curso termina, la lista se relee", async () => {
    active = { active: { phase: "running", run_id: "run-20260924-100000-bbbb" } };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderPage(client);
    await screen.findByRole("combobox", { name: "Corrida" });
    await waitFor(() => expect(client.getQueryData(labKeys.active())).toMatchObject({ active: { phase: "running" } }));
    const listReads = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/lab/runs")).length;
    const before = listReads();

    active = { active: null };
    await client.refetchQueries({ queryKey: labKeys.active() });

    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });
});
