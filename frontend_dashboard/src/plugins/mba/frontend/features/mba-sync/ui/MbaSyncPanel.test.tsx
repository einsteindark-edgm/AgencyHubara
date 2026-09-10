import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaSyncPanel } from "./MbaSyncPanel";

const fetchMock = vi.fn();

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const STATE_EMPTY = { agent_id: "sales", state: null };
const STATE_DONE = {
  agent_id: "sales",
  state: {
    ids: { skills: { persona: "s-1" } },
    last_apply: {
      at_ms: 1_700_000_000_000,
      status: "ok",
      fingerprint: "fp-0",
      counts: { changes: 3, ok: 3, failed: 0, skipped: 0 },
      results: [],
    },
  },
};
const op = (o: Record<string, unknown>) => ({
  body: {},
  remote_id: null,
  reason: "",
  connector_label: null,
  connector_remote_id: null,
  ...o,
});
const PLAN = {
  agent_id: "sales",
  entity_id: "PHONE_777",
  fingerprint: "fp-1",
  blocked: [],
  counts: { create: 1, update: 1, noop: 5, skip: 1 },
  ops: [
    op({ section: "skills", label: "persona", action: "update", body: { title: "persona" }, remote_id: "s-1" }),
    op({ section: "faqs", label: "¿Garantía?", action: "create", body: { question: "¿Garantía?", answer: "48 h" } }),
    op({ section: "settings", label: "settings", action: "noop", reason: "unchanged" }),
    op({ section: "allowlist", label: "+573001234567", action: "skip", reason: "d2.3" }),
  ],
};
const BLOCKED = { ...PLAN, fingerprint: "fp-b", blocked: ["entity_id_missing", "placeholder:<FLOW_ID>"] };

function routes(map: Record<string, (init?: RequestInit) => Response>) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${new URL(url, "http://x").pathname}`;
    const handler = map[key];
    if (!handler) return Promise.resolve(jsonResponse({ detail: `no route ${key}` }, 500));
    return Promise.resolve(handler(init));
  });
}

describe("MbaSyncPanel", () => {
  it("shows the last sync from the vault and does not fetch the plan until asked", async () => {
    routes({ "GET /api/mba/agents/sales/sync": () => jsonResponse(STATE_DONE) });
    renderWithClient(<MbaSyncPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/Último sync/));
    screen.getByText(/3 cambios aplicados/);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/sync/plan"))).toBe(false);
    screen.getByRole("button", { name: /Ver cambios/ });
  });

  it("reviews the plan, then applies only after the second confirmation with the plan fingerprint", async () => {
    const posts: unknown[] = [];
    routes({
      "GET /api/mba/agents/sales/sync": () => jsonResponse(STATE_EMPTY),
      "GET /api/mba/agents/sales/sync/plan": () => jsonResponse(PLAN),
      "POST /api/mba/agents/sales/sync": (init) => {
        posts.push(JSON.parse(String(init?.body)));
        return jsonResponse({
          agent_id: "sales",
          applied: true,
          reason: "applied",
          status: "ok",
          blocked: [],
          error: null,
          plan: { fingerprint: "fp-1" },
          results: [
            { section: "skills", label: "persona", action: "update", ok: true, remote_id: "s-1", error: null, skipped: null },
            { section: "faqs", label: "¿Garantía?", action: "create", ok: true, remote_id: "f-9", error: null, skipped: null },
          ],
          state: {},
        });
      },
    });
    renderWithClient(<MbaSyncPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/Nunca sincronizado/));
    fireEvent.click(screen.getByRole("button", { name: /Ver cambios/ }));
    await waitFor(() => screen.getByText("¿Garantía?"));
    screen.getByText("2 cambios", { selector: "b" });
    screen.getByText(/5 sin cambios/);
    screen.getByText(/1 fuera de alcance/);
    expect(posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Aplicar 2 cambios/ }));
    expect(posts).toHaveLength(0); // primer paso: solo abre la confirmación
    fireEvent.click(screen.getByRole("button", { name: /Confirmar envío a Meta/ }));
    await waitFor(() => screen.getByText(/Aplicado: 2 de 2/));
    expect(posts).toEqual([{ fingerprint: "fp-1" }]);
  });

  it("a blocked plan lists the reasons and offers no apply button", async () => {
    routes({
      "GET /api/mba/agents/sales/sync": () => jsonResponse(STATE_EMPTY),
      "GET /api/mba/agents/sales/sync/plan": () => jsonResponse(BLOCKED),
    });
    renderWithClient(<MbaSyncPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Ver cambios/ }));
    fireEvent.click(screen.getByRole("button", { name: /Ver cambios/ }));
    await waitFor(() => screen.getByText(/placeholder:<FLOW_ID>/));
    screen.getByText(/entity_id_missing/);
    expect(screen.queryByRole("button", { name: /Aplicar/ })).toBeNull();
  });

  it("a remote outage while planning is a visible error, never an empty diff", async () => {
    routes({
      "GET /api/mba/agents/sales/sync": () => jsonResponse(STATE_EMPTY),
      "GET /api/mba/agents/sales/sync/plan": () =>
        jsonResponse(
          { detail: { error: "remote_unavailable", kind: "not_configured", status: null, detail: "META_MBA_TOKEN no configurado" } },
          503,
        ),
    });
    renderWithClient(<MbaSyncPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Ver cambios/ }));
    fireEvent.click(screen.getByRole("button", { name: /Ver cambios/ }));
    await waitFor(() => screen.getByText(/No se pudo leer el estado en Meta/));
    expect(screen.queryByRole("button", { name: /Aplicar/ })).toBeNull();
  });

  it("a stale plan is reported and the review can be refreshed", async () => {
    routes({
      "GET /api/mba/agents/sales/sync": () => jsonResponse(STATE_EMPTY),
      "GET /api/mba/agents/sales/sync/plan": () => jsonResponse(PLAN),
      "POST /api/mba/agents/sales/sync": () =>
        jsonResponse({
          agent_id: "sales",
          applied: false,
          reason: "plan_changed",
          status: "",
          results: [],
          blocked: [],
          error: null,
          plan: { fingerprint: "fp-2" },
          state: {},
        }),
    });
    renderWithClient(<MbaSyncPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Ver cambios/ }));
    fireEvent.click(screen.getByRole("button", { name: /Ver cambios/ }));
    await waitFor(() => screen.getByRole("button", { name: /Aplicar 2 cambios/ }));
    fireEvent.click(screen.getByRole("button", { name: /Aplicar 2 cambios/ }));
    fireEvent.click(screen.getByRole("button", { name: /Confirmar envío a Meta/ }));
    await waitFor(() => screen.getByText(/El plan cambió/));
    screen.getByRole("button", { name: /Ver cambios/ });
  });
});
