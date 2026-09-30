import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import { threadSchema } from "@plugins/lab/frontend/entities/lab-run";

import { TurnTraceModal } from "./TurnTraceModal";

/**
 * Modal del hilo de un turno (plan §11.1, revisión 2026-09-29: el operador no
 * entendía «EST-06 · falla»). Tres pestañas:
 *  - Resultado (abre primero): lo que escribió el cliente, lo que respondió
 *    el bot y cómo le fue — lo que falló primero, por su NOMBRE, con el porqué
 *    y lo que se esperaba; lo que cumplió, plegado; los asuntos del cliente.
 *  - Paso a paso: el diagrama de secuencia y el detalle del paso.
 *  - Decisiones de Jev (solo si la traza las trae): quién decidió cada cosa.
 */

const RUN = "run-20260923-1041-ab12";
const thread = threadSchema.parse(threadFixture);
const turn = thread.turns[1];

const traceA0 = {
  fidelity: "v2",
  arm: "A0",
  rep: 0,
  trace: {},
  steps: [
    { i: 0, at_ms: 0, kind: "inbound", messages: turn.burst },
    { i: 1, at_ms: 10, dur_ms: 1900, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["send_shipping_rates"], text_fate: "discarded_default_deny", text: "¡Claro! te comparto el catálogo" },
    { i: 2, at_ms: 1950, dur_ms: 300, kind: "tool", name: "send_shipping_rates", ok: true },
    { i: 3, at_ms: 2300, kind: "cut", reason: "awaits_customer" },
  ],
};

const evaluations = {
  arm: "A0",
  rep: 0,
  episodes: [
    {
      session_id: thread.session_id,
      episode_id: "ep_1",
      verdict: "ALERTA",
      results: [
        { check_id: "EST-06", verdict: "falla", level: "menor", turn: 2, source: "code", evidence: "turno 2: narración descartada «¡Claro! te comparto el catálogo»" },
        { check_id: "ENV-02", verdict: "pasa", turn: null },
        { check_id: "EST-08", verdict: "falla", level: "mayor", turn: 2, source: "judge", critique: "No respondió lo del catálogo.", evidence: "Bogotá: $X.XXX", topics: [
          { topic: "catálogo", turn: 2, msg: 1, covered: false, evidence: "" },
          { topic: "envío a Bogotá", turn: 2, msg: 2, covered: true, evidence: "" },
        ] },
        { check_id: "DES-02", verdict: "pasa", turn: 2 },
        { check_id: "CIE-03", verdict: "sin_senal", turn: 2 },
        { check_id: "APE-01", verdict: "pasa", turn: 1 },
      ],
    },
  ],
};

const catalog = {
  registry_version: 4,
  checks: [
    { id: "EST-06", name: "Sin narración descartada", level: "menor", kind: "code", rule: "El texto que escribe el modelo sale; no se descarta." },
    { id: "EST-08", name: "Responde lo que el cliente preguntó", level: "mayor", kind: "judge", rule: "Cada asunto de la ráfaga recibe respuesta." },
    { id: "DES-02", name: "Una pregunta por burbuja", level: "menor", kind: "code", rule: "Ningún texto enviado contiene más de una pregunta." },
    { id: "CIE-03", name: "Etiqueta de pago pendiente", level: "mayor", kind: "code", rule: "…" },
    { id: "APE-01", name: "Saludo por hora y marca en el primer contacto", level: "mayor", kind: "code", rule: "…" },
    { id: "ENV-02", name: "Formulario sin responder al cliente", level: "mayor", kind: "code", rule: "…" },
  ],
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

function routes(trace: (u: string) => Promise<Response>) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/turns/trace")) return trace(u);
    if (u.includes("/evaluations")) return json(evaluations);
    if (u.endsWith("/api/lab/checks")) return json(catalog);
    if (u.includes(`/conversations/${thread.session_id}`)) return json(threadFixture);
    return json({}, 404);
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  routes((u) => (u.includes("arm=B") ? json({ detail: "Ese turno no tiene traza en este brazo." }, 404) : json(traceA0)));
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderModal(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TurnTraceModal run={RUN} sid={thread.session_id} turn={turn} arms={["A0", "B"]} initialArm="A0" onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("TurnTraceModal", () => {
  it("abre como diálogo con el turno, la ráfaga y el bot, en la pestaña Resultado", async () => {
    renderModal();

    const dialog = screen.getByRole("dialog", { name: "Hilo del turno 2" });
    expect(within(dialog).getByText(/2 mensajes seguidos/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Producción" })).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).getByRole("tab", { name: "Resultado" })).toHaveAttribute("aria-selected", "true");
  });

  it("muestra lo que escribió el cliente y lo que respondió el bot en ese turno", async () => {
    renderModal();

    const asked = screen.getByRole("region", { name: "Lo que escribió el cliente" });
    expect(within(asked).getByText("vi que también hacen velas con otros diseños, ¿me mandas el catálogo?")).toBeInTheDocument();
    expect(within(asked).getByText("y el envío a Bogotá cuánto sale?")).toBeInTheDocument();
    const answered = await screen.findByRole("region", { name: "Lo que respondió Producción" });
    expect(await within(answered).findByText(/Tarifas de envío/)).toBeInTheDocument();
  });

  it("lo que falló va primero, por su nombre, con el porqué y lo que se esperaba", async () => {
    renderModal();

    const result = await screen.findByRole("region", { name: "Cómo le fue en este turno" });
    expect(await within(result).findByText("Responde lo que el cliente preguntó")).toBeInTheDocument();
    expect(within(result).getByText("No respondió lo del catálogo.")).toBeVisible();
    expect(within(result).getByText("Cada asunto de la ráfaga recibe respuesta.")).toBeVisible();
    expect(within(result).getByText("Sin narración descartada")).toBeVisible();
    expect(within(result).getByText("Narración descartada «¡Claro! te comparto el catálogo»")).toBeVisible();
    // Nada de códigos sueltos: el código queda como referencia pequeña.
    expect(within(result).queryByText("EST-06 · falla")).toBeNull();
    // Lo que cumplió, plegado; lo que no se decide con un turno, contado.
    expect(within(result).getByText("Cumplió 1 check")).toBeVisible();
    expect(within(result).getByText("Una pregunta por burbuja")).not.toBeVisible();
    expect(within(result).getByText("1 check no se puede decidir con este turno solo: depende de lo que pase después.")).toBeInTheDocument();
  });

  it("el encabezado dice cómo le fue a ese bot en el turno, en cualquier pestaña", async () => {
    renderModal();

    const verdict = await screen.findByRole("group", { name: "Resultado de este bot en el turno" });
    expect(await within(verdict).findByText("ALERTA")).toBeInTheDocument();
    expect(within(verdict).getByText("Fallan 2 de 3 checks")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Paso a paso" }));
    expect(within(screen.getByRole("group", { name: "Resultado de este bot en el turno" })).getByText("Fallan 2 de 3 checks")).toBeInTheDocument();
  });

  it("los asuntos del cliente dicen cuáles se respondieron", async () => {
    renderModal();

    const topics = await screen.findByRole("region", { name: "Asuntos del cliente" });
    expect(within(topics).getByText("catálogo")).toBeInTheDocument();
    expect(within(topics).getByText("sin responder")).toBeInTheDocument();
    expect(within(topics).getByText("envío a Bogotá")).toBeInTheDocument();
    expect(within(topics).getByText("respondido")).toBeInTheDocument();
  });

  it("Paso a paso: el primer paso sale seleccionado y clic en otro cambia el detalle", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("tab", { name: "Paso a paso" }));
    const seq = await screen.findByRole("group", { name: "Secuencia de pasos del turno" });

    expect(screen.getByRole("heading", { level: 4, name: /^1\. El cliente escribió 2 mensajes seguidos/ })).toBeInTheDocument();
    fireEvent.click(within(seq).getByRole("button", { name: "Paso 3: Pide enviar las tarifas de envío" }));
    expect(screen.getByRole("heading", { level: 4, name: "3. Pide enviar las tarifas de envío" })).toBeInTheDocument();
    expect(screen.getByText("No se envió: venía junto a una herramienta")).toBeInTheDocument();
  });

  it("Paso a paso: la ronda dice qué recibe el modelo y lo que pidió dice qué herramienta corrió", async () => {
    // Operador, 2026-09-30: en la ronda «no sabemos qué le está enviando»;
    // en lo que pidió, «cuál fue la herramienta que ejecutó».
    renderModal();
    fireEvent.click(screen.getByRole("tab", { name: "Paso a paso" }));
    const seq = await screen.findByRole("group", { name: "Secuencia de pasos del turno" });

    fireEvent.click(within(seq).getByRole("button", { name: "Paso 2: Ronda 1 del modelo" }));
    expect(screen.getByText("Lo que recibe el modelo")).toBeInTheDocument();
    // El texto sale renglón por renglón; la búsqueda compara con los espacios juntos.
    expect(screen.getByText(turn.burst.map((m) => m.text).join(" "))).toBeInTheDocument();

    fireEvent.click(within(seq).getByRole("button", { name: "Paso 3: Pide enviar las tarifas de envío" }));
    const panel = screen.getByRole("heading", { level: 4, name: "3. Pide enviar las tarifas de envío" }).parentElement as HTMLElement;
    expect(within(panel).getByText("send_shipping_rates")).toBeInTheDocument();
  });

  it("cambiar de bot pide la traza de ese bot y dice si no la hay", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Bot nuevo con Jev" }));
    fireEvent.click(screen.getByRole("tab", { name: "Paso a paso" }));

    expect(await screen.findByText("Este bot no tiene el paso a paso de este turno.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("arm=B"))).toBe(true);
  });

  it("con el bot nuevo aparece la pestaña de las decisiones de Jev", async () => {
    const traceB = {
      ...traceA0,
      arm: "B",
      trace: {
        decisions: [
          { stage: "ingest", message: 1, capability: "compra", by: "jev", provider: "jev", value: [null, "text"],
            answers: [{ q: "compra.que_hace", choice: "pregunta", confidence: 0.91 }] },
          { stage: "turno", capability: "saludo", by: "respaldo", provider: "jev", value: false, reason: "timeout" },
        ],
      },
    };
    routes((u) => json(u.includes("arm=B") ? traceB : traceA0));
    renderModal();
    // En producción (A0) la traza no trae decisiones del motor: no hay pestaña.
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/turns/trace"))).toBe(true));
    expect(screen.queryByRole("tab", { name: "Decisiones de Jev" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Bot nuevo con Jev" }));
    fireEvent.click(await screen.findByRole("tab", { name: "Decisiones de Jev" }));

    const section = await screen.findByRole("region", { name: "Decisiones de Jev" });
    expect(within(section).getByText("Jev decidió 1 de 2 · 1 cayó a la regla porque Jev falló")).toBeInTheDocument();
    const read = within(section).getByRole("list", { name: "Al leer el mensaje 1" });
    expect(within(read).getByText("No hay señal de compra")).toBeInTheDocument();
    expect(within(read).getByText("Jev")).toBeInTheDocument();
    expect(within(read).getByText("¿Qué hace el cliente con su mensaje? pregunta (91 %)")).toBeInTheDocument();
    const turnList = within(section).getByRole("list", { name: "Durante el turno" });
    expect(within(turnList).getByText("No hacía falta agregar la bienvenida")).toBeInTheDocument();
    expect(within(turnList).getByText("La regla (Jev no respondió a tiempo)")).toBeInTheDocument();
  });

  it("el botón de cerrar y Escape cierran", async () => {
    const onClose = renderModal();

    fireEvent.click(screen.getByRole("button", { name: "Cerrar el hilo" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
