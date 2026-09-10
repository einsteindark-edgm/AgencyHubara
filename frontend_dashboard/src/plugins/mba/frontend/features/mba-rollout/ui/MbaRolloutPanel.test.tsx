import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaRolloutPanel } from "./MbaRolloutPanel";

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

const check = (code: string, ok: boolean, detail = "") => ({ code, ok, detail });
const READY = {
  agent_id: "sales",
  entity_id: "PHONE_777",
  rollout_enabled: false,
  ai_audience: "ALLOWLISTED_ONLY",
  allowlist: [{ id: "e-1", phone: "+573001234567", in_hubara: true }],
  checks: [
    check("flag_enabled", true),
    check("sync_ok", true),
    check("connector_active", true, "connector en Meta: ACTIVE"),
    check("audience_allowlisted_only", true),
    check("allowlist_nonempty", true),
    check("allowlist_within_hubara", true),
  ],
  can_enable: true,
  everyone_allowed: false,
  last_sync: { status: "ok", at_ms: 1_700_000_000_000 },
  history: [],
};
const NOT_READY = {
  ...READY,
  allowlist: [...READY.allowlist, { id: "e-2", phone: "+573000000000", in_hubara: false }],
  checks: READY.checks.map((c) =>
    c.code === "sync_ok"
      ? check("sync_ok", false, "el último sync con Meta terminó OK (D2.2)")
      : c.code === "allowlist_within_hubara"
        ? check("allowlist_within_hubara", false, "fuera de la lista cerrada de Hubara: +573000000000")
        : c,
  ),
  can_enable: false,
};
const ok = (over: Record<string, unknown> = {}) => ({
  agent_id: "sales",
  applied: true,
  reason: "applied",
  blocked: [],
  error: null,
  checks: [],
  ...over,
});

function routes(map: Record<string, (init?: RequestInit) => Response>, log: string[] = []) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${new URL(url, "http://x").pathname}`;
    log.push(`${key} ${init?.body ? String(init.body) : ""}`.trim());
    const handler = map[key];
    if (!handler) return Promise.resolve(jsonResponse({ detail: `no route ${key}` }, 500));
    return Promise.resolve(handler(init));
  });
}

describe("MbaRolloutPanel", () => {
  it("shows the state in Meta, the readiness checklist and the allowlist with Hubara membership", async () => {
    routes({ "GET /api/mba/agents/sales/rollout": () => jsonResponse(NOT_READY) });
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/MBA apagado/));
    screen.getByText("ALLOWLISTED_ONLY", { selector: ".mono" });
    screen.getByText("+573001234567");
    screen.getByText("+573000000000");
    expect(screen.getAllByText(/fuera de la lista cerrada de Hubara/).length).toBeGreaterThanOrEqual(2); // en el chequeo y en la entrada
    screen.getByText(/el último sync con Meta terminó OK/);
    // no está listo: el botón de encender no existe
    expect(screen.queryByRole("button", { name: /Encender MBA/ })).toBeNull();
    screen.getByText(/2 de 6 chequeos pendientes/);
  });

  it("enabling needs two steps and sends confirm=true; disabling is one click without confirm", async () => {
    const log: string[] = [];
    routes(
      {
        "GET /api/mba/agents/sales/rollout": () => jsonResponse(READY),
        "PUT /api/mba/agents/sales/rollout/enabled": () => jsonResponse(ok()),
      },
      log,
    );
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Encender MBA/ }));
    fireEvent.click(screen.getByRole("button", { name: /Encender MBA/ }));
    expect(log.filter((l) => l.startsWith("PUT"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar: encender para la allowlist/ }));
    await waitFor(() => expect(log.filter((l) => l.startsWith("PUT"))).toHaveLength(1));
    expect(log.find((l) => l.startsWith("PUT"))).toContain('{"enabled":true,"confirm":true}');

    routes(
      {
        "GET /api/mba/agents/sales/rollout": () => jsonResponse({ ...READY, rollout_enabled: true }),
        "PUT /api/mba/agents/sales/rollout/enabled": () => jsonResponse(ok()),
      },
      log,
    );
    cleanup();
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/MBA encendido/));
    fireEvent.click(screen.getByRole("button", { name: /Apagar MBA/ }));
    await waitFor(() => expect(log.filter((l) => l.includes('"enabled":false'))).toHaveLength(1));
    expect(log.find((l) => l.includes('"enabled":false'))).toContain('{"enabled":false}');
  });

  it("EVERYONE is not offered when the policy knob is off, and needs confirmation when it is", async () => {
    routes({ "GET /api/mba/agents/sales/rollout": () => jsonResponse(READY) });
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByText("ALLOWLISTED_ONLY", { selector: ".mono" }));
    expect(screen.queryByRole("button", { name: /EVERYONE/ })).toBeNull();
    screen.getByText(/EVERYONE deshabilitado por política/);

    const log: string[] = [];
    cleanup();
    routes(
      {
        "GET /api/mba/agents/sales/rollout": () => jsonResponse({ ...READY, everyone_allowed: true }),
        "PUT /api/mba/agents/sales/rollout/audience": () => jsonResponse(ok()),
      },
      log,
    );
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Abrir a EVERYONE/ }));
    fireEvent.click(screen.getByRole("button", { name: /Abrir a EVERYONE/ }));
    expect(log.filter((l) => l.startsWith("PUT"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar: EVERYONE/ }));
    await waitFor(() => expect(log.filter((l) => l.startsWith("PUT"))).toHaveLength(1));
    expect(log.find((l) => l.startsWith("PUT"))).toContain('{"ai_audience":"EVERYONE","confirm":true}');
  });

  it("adds a phone through the API and shows the policy refusal verbatim", async () => {
    const log: string[] = [];
    let calls = 0;
    routes(
      {
        "GET /api/mba/agents/sales/rollout": () => jsonResponse(READY),
        "POST /api/mba/agents/sales/rollout/allowlist": () =>
          jsonResponse(++calls === 1 ? ok() : ok({ applied: false, reason: "customer_not_in_hubara_allowlist" })),
      },
      log,
    );
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByPlaceholderText("+573001234567"));
    fireEvent.change(screen.getByPlaceholderText("+573001234567"), { target: { value: "+573009876543" } });
    fireEvent.click(screen.getByRole("button", { name: /Agregar/ }));
    await waitFor(() => expect(log.filter((l) => l.startsWith("POST"))).toHaveLength(1));
    expect(log.find((l) => l.startsWith("POST"))).toContain('{"phone":"+573009876543"}');
    fireEvent.change(screen.getByPlaceholderText("+573001234567"), { target: { value: "+573000000000" } });
    fireEvent.click(screen.getByRole("button", { name: /Agregar/ }));
    await waitFor(() => screen.getByText(/no está en la lista cerrada de Hubara/));
  });

  it("removing a phone is a two-step inline confirmation", async () => {
    const log: string[] = [];
    routes(
      {
        "GET /api/mba/agents/sales/rollout": () => jsonResponse(READY),
        "DELETE /api/mba/agents/sales/rollout/allowlist/e-1": () => jsonResponse(ok()),
      },
      log,
    );
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByRole("button", { name: /Quitar/ }));
    fireEvent.click(screen.getByRole("button", { name: /Quitar/ }));
    expect(log.filter((l) => l.startsWith("DELETE"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar quitar/ }));
    await waitFor(() => expect(log.filter((l) => l.startsWith("DELETE"))).toHaveLength(1));
  });

  it("drift while MBA is on is a loud alert next to the kill switch, and the history is visible", async () => {
    routes({
      "GET /api/mba/agents/sales/rollout": () =>
        jsonResponse({
          ...NOT_READY,
          rollout_enabled: true,
          drift: ["sync_ok", "allowlist_within_hubara"],
          history: [
            { at_ms: 1_700_000_000_000, action: "rollout_enabled", value: "true", ok: true },
            { at_ms: 1_700_000_100_000, action: "allowlist_add", value: "+573009876543", ok: false, error: { kind: "rejected", detail: "dup", status: 409 } },
          ],
        }),
    });
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/MBA encendido/));
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/MBA está encendido y 2 chequeos dejaron de cumplirse/);
    screen.getByRole("button", { name: /Apagar MBA/ });
    expect(screen.queryByText(/chequeos pendientes: no se puede encender/)).toBeNull();
    screen.getByText(/rollout_enabled → true/);
    screen.getByText(/allowlist_add → \+573009876543/);
    screen.getByText(/rejected dup/);
  });

  it("a remote outage is a visible error with the real reason", async () => {
    routes({
      "GET /api/mba/agents/sales/rollout": () =>
        jsonResponse({ detail: { error: "remote_unavailable", kind: "not_configured", status: null, detail: "META_MBA_TOKEN no configurado" } }, 503),
    });
    renderWithClient(<MbaRolloutPanel agentId="sales" />);
    await waitFor(() => screen.getByText(/No se pudo leer el rollout en Meta/));
    screen.getByText(/META_MBA_TOKEN no configurado/);
  });
});
