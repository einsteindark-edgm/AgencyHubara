import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { LabConversations } from "./LabConversations";

/**
 * Pestaña "Conversaciones" (diseño §09; revisión 2026-09-29: el operador no
 * entendía los códigos). La lista del banco con el resultado de cada bot; el
 * hilo con un selector de bot y el modo Comparar (cada turno con lo que
 * respondió cada bot, lado a lado, y cómo le fue); a la derecha, cómo le fue a
 * cada bot en la conversación y qué falló, por nombre. Cada turno abre el
 * modal del hilo del turno.
 */

const RUN = "run-20260923-1041-ab12";
const SID = "wa_573001234567";
const OTHER = "wa_573007654321";
const run: LabRun = {
  run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 1, registry_version: 3, counts: {}, phase: "done",
  turns_done: null, turns_total: null, spent_usd: null, error: null, notes: [], started_at_ms: null, updated_at_ms: null,
};

const catalog = {
  registry_version: 4,
  checks: [
    { id: "EST-06", name: "Sin narración descartada", level: "menor", kind: "code", rule: "El texto del modelo sale." },
    { id: "APE-01", name: "Saludo por hora y marca en el primer contacto", level: "mayor", kind: "code", rule: "Abre con saludo por hora." },
    { id: "DES-04", name: "Clasificó bien la intención del cliente", level: "mayor", kind: "judge", rule: "Responde a lo que el cliente buscaba." },
    { id: "CIE-03", name: "Etiqueta de pago pendiente", level: "mayor", kind: "code", rule: "…" },
  ],
};

const evaluationsA0 = {
  arm: "A0", rep: 0,
  episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "ALERTA", results: [
    { check_id: "EST-06", verdict: "falla", level: "menor", turn: 2, evidence: "turno 2: narración descartada «¡Claro!»" },
    { check_id: "APE-01", verdict: "pasa", turn: 1, evidence: "Saludó según la hora." },
    { check_id: "CON-05", verdict: "no_aplica", turn: null },
  ] }],
};

const evaluationsA1 = {
  arm: "A1", rep: 0,
  episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "ALERTA", results: [
    { check_id: "DES-04", verdict: "falla", level: "mayor", turn: 2, source: "judge", critique: "Ofreció otro diseño." },
    { check_id: "APE-01", verdict: "pasa", turn: 1 },
  ] }],
};

const withA1 = {
  ...threadFixture,
  turns: threadFixture.turns.map((t) => ({
    ...t,
    outputs: { ...t.outputs, A1: { sent_texts: [`respuesta del bot actual al turno ${t.turn}`], tools: [], discarded_narration: [], guards: [], suppressed_reason: null, llm_text: null } },
  })),
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith(`/runs/${RUN}/conversations`)) {
      return json({
        conversations: [
          { session_id: SID, turns: 2, episodes: ["ep_1"], last_at_ms: 1790178069000, verdicts: { A0: { ep_1: "ALERTA" }, A1: { ep_1: "ALERTA" } } },
          { session_id: OTHER, turns: 5, episodes: ["ep_1", "ep_2"], last_at_ms: 1790090000000, verdicts: { A0: { ep_1: "PASA", ep_2: "FALLA" } } },
        ],
      });
    }
    if (u.endsWith("/api/lab/checks")) return json(catalog);
    if (u.includes(`/conversations/${SID}/turns/trace`)) {
      return json({ fidelity: "v1", arm: "A0", rep: 0, trace: {}, steps: [{ i: 1, at_ms: null, kind: "inbound", messages: [{ text: "hola" }] }] });
    }
    if (u.includes(`/conversations/${SID}/evaluations`)) {
      if (u.includes("arm=A1")) return json(evaluationsA1);
      if (u.includes("arm=B")) return json({ arm: "B", rep: 0, episodes: [] });
      return json(evaluationsA0);
    }
    if (u.includes(`/conversations/${SID}`)) return json(withA1);
    return json({ detail: "no" }, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LabConversations run={run} />
    </QueryClientProvider>,
  );
}

describe("LabConversations", () => {
  it("lista las conversaciones del banco con el resultado de cada bot en su línea, sin el teléfono", async () => {
    renderTab();
    const list = await screen.findByRole("list", { name: "Conversaciones del banco" });

    const items = within(list).getAllByRole("button");
    expect(items[0]).toHaveTextContent("Cliente ···4567");
    expect(items[0]).toHaveTextContent(/Producción\s*ALERTA/);
    expect(items[0]).toHaveTextContent(/Bot actual simulado\s*ALERTA/);
    expect(items[0]).toHaveTextContent(/Bot nuevo con Jev\s*sin resultado/);
    expect(items[1]).toHaveTextContent(/Producción\s*FALLA/);
    expect(items[1]).toHaveTextContent("5 turnos · 2 episodios");
    expect(items[0]).toHaveAttribute("aria-current", "true");
    expect(document.body.textContent).not.toContain("573001234567");
  });

  it("muestra el hilo de producción con la ráfaga agrupada y el botón de cada turno", async () => {
    renderTab();

    expect(await screen.findByText("Buenas tardes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ver el hilo de la ráfaga de 2 mensajes" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Ver hilo del turno/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Producción" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Lo que respondió el bot de verdad.")).toBeInTheDocument();
  });

  it("cambiar de bot muestra lo que ese bot respondió (o que todavía no corrió)", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("button", { name: "Bot nuevo con Jev" }));

    expect(screen.getAllByText("Este bot todavía no respondió este turno.")).toHaveLength(2);
  });

  it("Comparar pone lado a lado lo que respondió cada bot en cada turno, con su resultado y lo que falló", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("button", { name: "Comparar" }));

    const turn2 = await screen.findByRole("region", { name: "Turno 2" });
    const prod = within(turn2).getByRole("group", { name: "Producción" });
    expect(within(prod).getByText(/Tarifas de envío/)).toBeInTheDocument();
    expect(await within(prod).findByText("PASA")).toBeInTheDocument();
    expect(within(prod).getByText("Sin narración descartada")).toBeInTheDocument();
    const current = within(turn2).getByRole("group", { name: "Bot actual simulado" });
    expect(within(current).getByText("respuesta del bot actual al turno 2")).toBeInTheDocument();
    expect(await within(current).findByText("ALERTA")).toBeInTheDocument();
    expect(within(current).getByText("Clasificó bien la intención del cliente")).toBeInTheDocument();
    const fresh = within(turn2).getByRole("group", { name: "Bot nuevo con Jev" });
    expect(within(fresh).getByText("Este bot todavía no respondió este turno.")).toBeInTheDocument();
  });

  it("en Comparar, el botón de cada bot abre el turno con ese bot", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("button", { name: "Comparar" }));
    const turn2 = await screen.findByRole("region", { name: "Turno 2" });
    const current = within(turn2).getByRole("group", { name: "Bot actual simulado" });

    fireEvent.click(within(current).getByRole("button", { name: "Ver cómo lo decidió" }));

    const dialog = await screen.findByRole("dialog", { name: "Hilo del turno 2" });
    expect(within(dialog).getByRole("button", { name: "Bot actual simulado" })).toHaveAttribute("aria-pressed", "true");
  });

  it("el botón «Ver hilo del turno» dice el mismo resultado que el modal de ese turno", async () => {
    // Caso real (···6543, producción): el turno 1 solo falló un check menor
    // (PASA) y se pintaba en ámbar; el turno 2 falló uno mayor (ALERTA) y se
    // pintaba gris. El color y la etiqueta salen de la evaluación del turno.
    const base = fetchMock.getMockImplementation();
    fetchMock.mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes(`/conversations/${SID}/evaluations`) && u.includes("arm=A0")) {
        return json({ arm: "A0", rep: 0, episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "ALERTA", results: [
          { check_id: "EST-06", verdict: "falla", level: "menor", turn: 1, evidence: "turno 1: narración descartada" },
          { check_id: "APE-01", verdict: "pasa", turn: 1 },
          { check_id: "DES-04", verdict: "falla", level: "mayor", turn: 2, source: "judge", critique: "Ofreció otro diseño." },
        ] }] });
      }
      return base!(url);
    });
    renderTab();
    await screen.findByText("Buenas tardes");

    const chips = screen.getAllByRole("button", { name: /^Ver hilo del turno/ });
    expect(await within(chips[0]).findByText("PASA")).toBeInTheDocument();
    expect(await within(chips[1]).findByText("ALERTA")).toBeInTheDocument();

    fireEvent.click(chips[1]);
    const dialog = await screen.findByRole("dialog", { name: "Hilo del turno 2" });
    const verdict = await within(dialog).findByRole("group", { name: "Resultado de este bot en el turno" });
    expect(within(verdict).getByText("ALERTA")).toBeInTheDocument();
  });

  it("mientras carga la evaluación, el botón no dice «sin evaluar» (todavía no se sabe)", async () => {
    const base = fetchMock.getMockImplementation();
    fetchMock.mockImplementation((url: string) =>
      String(url).includes(`/conversations/${SID}/evaluations`) ? new Promise<Response>(() => {}) : base!(url),
    );
    renderTab();
    await screen.findByText("Buenas tardes");

    const chips = screen.getAllByRole("button", { name: /^Ver hilo del turno/ });
    expect(within(chips[0]).queryByText("sin evaluar")).toBeNull();
    expect(within(chips[0]).getByLabelText("Cargando la evaluación")).toBeInTheDocument();
  });

  it("el botón del turno abre el modal del hilo de ese turno", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getAllByRole("button", { name: /^Ver hilo del turno/ })[1]);

    expect(await screen.findByRole("dialog", { name: "Hilo del turno 2" })).toBeInTheDocument();
  });

  it("a la derecha: cómo le fue a cada bot en la conversación, y qué significa cada resultado", async () => {
    renderTab();
    const side = await screen.findByRole("tabpanel", { name: "Por bot" });

    expect(within(side).getByText("Producción").closest("li")).toHaveTextContent("ALERTA");
    expect(within(side).getByText("Bot nuevo con Jev").closest("li")).toHaveTextContent("todavía no corrió");
    expect(within(side).getByText("Falló algo importante (un check mayor).")).toBeInTheDocument();
  });

  it("«Qué falló» lista los fallos del bot elegido por nombre y turno; lo que cumplió, plegado", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("tab", { name: "Qué falló" }));

    const panel = await screen.findByRole("tabpanel", { name: "Qué falló" });
    const failed = await within(panel).findByRole("list", { name: "Lo que falló" });
    expect(within(failed).getByText("Sin narración descartada")).toBeInTheDocument();
    expect(within(failed).getByText("turno 2")).toBeInTheDocument();
    expect(within(failed).getByText("Narración descartada «¡Claro!»")).toBeInTheDocument();
    expect(within(panel).getByText("Cumplió siempre 1 check")).toBeVisible();
    expect(within(panel).queryByText("CON-05")).toBeNull();
  });

  it("un check que se repite por turno cuenta una vez entre lo cumplido; los que dependen de después, aparte", async () => {
    const base = fetchMock.getMockImplementation();
    fetchMock.mockImplementation((url: string) =>
      String(url).includes(`/conversations/${SID}/evaluations`)
        ? json({
            arm: "A0", rep: 0,
            episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "PASA", results: [
              { check_id: "EST-06", verdict: "pasa", turn: 1 },
              { check_id: "EST-06", verdict: "pasa", turn: 2 },
              { check_id: "CIE-03", verdict: "sin_senal", turn: 2 },
            ] }],
          })
        : base!(url),
    );
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("tab", { name: "Qué falló" }));

    const panel = await screen.findByRole("tabpanel", { name: "Qué falló" });
    expect(await within(panel).findByText("No falló ningún check.")).toBeInTheDocument();
    expect(within(panel).getByText("Cumplió siempre 1 check")).toBeVisible();
    expect(within(panel).getByText("1 check depende de lo que pasó después de cada turno y no se decidió.")).toBeInTheDocument();
  });

  it("elegir otra conversación la marca", async () => {
    renderTab();
    const list = await screen.findByRole("list", { name: "Conversaciones del banco" });
    fireEvent.click(within(list).getAllByRole("button")[1]);

    expect(within(list).getAllByRole("button")[1]).toHaveAttribute("aria-current", "true");
  });
});
