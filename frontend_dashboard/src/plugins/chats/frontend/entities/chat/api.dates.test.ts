/**
 * Fechas del adaptador de Chats.
 *
 * Dos bugs reportados por el operador (2026-09-10):
 *   1. El inbox mostraba la hora en el timezone del NAVEGADOR y tiraba el
 *      timestamp crudo, así que no había con qué filtrar por fecha ni con qué
 *      decidir qué es "hoy".
 *   2. El panel central metía UN solo separador ("Conversación") para todo el
 *      historial — no se sabía de cuándo era cada mensaje.
 *
 * Todo debe quedar anclado a America/Bogota, sin importar dónde corra el
 * navegador del operador.
 */
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createElement } from "react";

import { useChatInbox, useChatMessages } from "./api";
import type { ChatSession } from "@plugins/chats/frontend/entities/session";
import type { ChatMessage } from "@plugins/chats/frontend/entities/message";

/** 2026-09-08T02:00:00Z === 2026-09-07 21:00 en Bogotá (UTC-5). */
const LATE_NIGHT_UNIX = Date.UTC(2026, 8, 8, 2, 0, 0) / 1000;
/** 2026-09-08T15:30:00Z === 2026-09-08 10:30 en Bogotá. */
const NEXT_MORNING_UNIX = Date.UTC(2026, 8, 8, 15, 30, 0) / 1000;

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    session_id: "wa_573001112233",
    phone_number: "573001112233",
    tag: "INTERESADO",
    motivo: "Cliente preguntó por lavanda",
    active_agent_route: "ventas",
    phone_number_id: null,
    pending_payment_order_id: null,
    last_updated_timestamp: LATE_NIGHT_UNIX,
    origin: null,
    ...overrides,
  };
}

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return createElement(QueryClientProvider, { client: qc }, children);
}

function mockJson(payload: unknown) {
  globalThis.fetch = (() =>
    Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )) as unknown as typeof fetch;
}

async function flush() {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 10));
}

async function runInbox(sessions: ChatSession[]) {
  mockJson({ sessions });
  const { result } = renderHook(() => useChatInbox(), { wrapper: wrap });
  await flush();
  return result.current.data;
}

async function runMessages(messages: ChatMessage[]) {
  mockJson({
    session_id: "wa_573001112233",
    phone_number: "573001112233",
    tag: "INTERESADO",
    motivo: "",
    memory_content: null,
    active_agent_route: "ventas",
    phone_number_id: null,
    pending_payment_order_id: null,
    origin: null,
    status_history: [],
    messages,
  });
  const { result } = renderHook(() => useChatMessages("wa_573001112233"), {
    wrapper: wrap,
  });
  await flush();
  return result.current.data;
}

describe("inbox: el item conserva el instante crudo y su día colombiano", () => {
  it("expone el timestamp unix del backend sin transformar", async () => {
    const data = await runInbox([makeSession()]);
    expect(data?.[0]?.timestamp).toBe(LATE_NIGHT_UNIX);
  });

  it("dayIso es el día EN BOGOTÁ, no el día UTC", async () => {
    // En UTC este instante es el 8; en Colombia todavía es el 7.
    const data = await runInbox([makeSession()]);
    expect(data?.[0]?.dayIso).toBe("2026-09-07");
  });

  it("time se formatea en hora Colombia (24h), no en el TZ del navegador", async () => {
    const data = await runInbox([makeSession()]);
    expect(data?.[0]?.time).toBe("21:00");
  });
});

describe("conversación: separadores de día estilo WhatsApp", () => {
  const twoDays: ChatMessage[] = [
    { ui_type: "user_message", role: "user", content: "Hola", timestamp: LATE_NIGHT_UNIX },
    {
      ui_type: "agent_message",
      role: "assistant",
      content: "¡Hola! ¿En qué te ayudo?",
      timestamp: LATE_NIGHT_UNIX + 60,
    },
    {
      ui_type: "user_message",
      role: "user",
      content: "Quiero la vela de lavanda",
      timestamp: NEXT_MORNING_UNIX,
    },
  ];

  it("inserta UN separador por día calendario colombiano", async () => {
    const data = await runMessages(twoDays);
    const days = data.filter((m) => m.kind === "day");
    expect(days.map((d) => d.dayIso)).toEqual(["2026-09-07", "2026-09-08"]);
  });

  it("el separador precede a los mensajes de SU día", async () => {
    const data = await runMessages(twoDays);
    expect(data.map((m) => m.kind)).toEqual(["day", "in", "out", "day", "in"]);
  });

  it("ya no emite el separador genérico 'Conversación'", async () => {
    const data = await runMessages(twoDays);
    expect(data.some((m) => m.text === "Conversación")).toBe(false);
  });

  it("mensajes del mismo día comparten un único separador", async () => {
    const data = await runMessages(twoDays.slice(0, 2));
    expect(data.filter((m) => m.kind === "day")).toHaveLength(1);
  });

  it("la hora de cada burbuja es hora Colombia", async () => {
    const data = await runMessages(twoDays);
    expect(data.find((m) => m.kind === "in")?.time).toBe("21:00");
    expect(data[data.length - 1]?.time).toBe("10:30");
  });

  it("acepta timestamps ISO string (backend viejo) y los ancla a Bogotá", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "Hola",
        timestamp: new Date(LATE_NIGHT_UNIX * 1000).toISOString(),
      },
    ]);
    expect(data.find((m) => m.kind === "day")?.dayIso).toBe("2026-09-07");
    expect(data.find((m) => m.kind === "in")?.time).toBe("21:00");
  });

  it("un historial sin timestamps no crashea ni inventa un día", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "Hola" },
    ]);
    expect(data.filter((m) => m.kind === "day")).toHaveLength(0);
    expect(data.filter((m) => m.kind === "in")).toHaveLength(1);
  });
});
