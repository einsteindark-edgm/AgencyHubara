/**
 * Tests del adapter `entities/chat/api.ts`. Cubren la traducción de tags
 * backend → inbox (regression del bug "venta procesada aparecía como Frío").
 *
 * El backend Python emite tags semánticamente del workflow (manage_conversation_tag):
 * COMPRA_EXITOSA, RECHAZO, CONFIRMADO_SIN_DATOS, INTERESADO, NO_ETIQUETADO, HUMANO.
 * El inbox del dashboard usa tags semánticos UX:
 * HUMANO, INTERESADO, PENDIENTE, CLIENTE, REMARKETING, FRÍO.
 *
 * Antes del fix, cualquier tag fuera de KNOWN_TAGS caía a `FRÍO`. Una venta
 * cerrada (tag=COMPRA_EXITOSA) aparecía como Frío en el inbox aunque la orden
 * estuviera procesada — el operador lo confundía con un cliente sin interés.
 */
import { describe, expect, it } from "vitest";
import { useChatInbox, useChatMessages } from "./api";
import type { ChatSession } from "@/entities/session";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createElement } from "react";

// Fixture base de una sesión backend
function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    session_id: "wa_573001112233",
    phone_number: "573001112233",
    tag: "INTERESADO",
    motivo: "Cliente preguntó por lavanda",
    active_agent_route: "ventas",
    phone_number_id: null,
    pending_payment_order_id: null,
    last_updated_timestamp: 1716700000,
    ...overrides,
  };
}

// Mock del fetch del inbox para inyectar fixtures
function mockSessionsResponse(sessions: ChatSession[]) {
  globalThis.fetch = (() =>
    Promise.resolve(
      new Response(JSON.stringify({ sessions }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )) as unknown as typeof fetch;
}

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return createElement(QueryClientProvider, { client: qc }, children);
}

async function runInbox(sessions: ChatSession[]) {
  mockSessionsResponse(sessions);
  const { result } = renderHook(() => useChatInbox(), { wrapper: wrap });
  // Esperar al fetch
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 10));
  return result.current.data;
}

describe("normalizeTag via useChatInbox (regression: venta=Frío bug)", () => {
  it("COMPRA_EXITOSA del backend → CLIENTE en inbox (no FRÍO)", async () => {
    const data = await runInbox([
      makeSession({ tag: "COMPRA_EXITOSA", motivo: "Cliente compró vela cruz-de-vida" }),
    ]);
    expect(data?.[0]?.tag).toBe("CLIENTE");
    expect(data?.[0]?.tagClass).toBe("t-cli");
  });

  it("RECHAZO del backend → FRÍO en inbox (semántica real de Frío)", async () => {
    const data = await runInbox([
      makeSession({ tag: "RECHAZO", motivo: "Cliente dijo que muy caro" }),
    ]);
    expect(data?.[0]?.tag).toBe("FRÍO");
    expect(data?.[0]?.tagClass).toBe("t-cold");
  });

  it("CONFIRMADO_SIN_DATOS → PENDIENTE en inbox", async () => {
    const data = await runInbox([
      makeSession({ tag: "CONFIRMADO_SIN_DATOS", motivo: "Confirmó pero faltó shipping" }),
    ]);
    expect(data?.[0]?.tag).toBe("PENDIENTE");
    expect(data?.[0]?.tagClass).toBe("t-pen");
  });

  it("CONFIRMADO_PAGO_PENDIENTE → PENDIENTE en inbox (orden registrada, pago sin verificar)", async () => {
    const data = await runInbox([
      makeSession({
        tag: "CONFIRMADO_PAGO_PENDIENTE",
        motivo: "Pedido draft_01ABC registrado, falta verificar transferencia",
      }),
    ]);
    expect(data?.[0]?.tag).toBe("PENDIENTE");
    expect(data?.[0]?.tagClass).toBe("t-pen");
  });

  it("INTERESADO se mantiene como INTERESADO", async () => {
    const data = await runInbox([makeSession({ tag: "INTERESADO" })]);
    expect(data?.[0]?.tag).toBe("INTERESADO");
    expect(data?.[0]?.tagClass).toBe("t-int");
  });

  it("NO_ETIQUETADO (default backend cuando no hay tag) → PENDIENTE en inbox", async () => {
    const data = await runInbox([makeSession({ tag: "NO_ETIQUETADO" })]);
    expect(data?.[0]?.tag).toBe("PENDIENTE");
    expect(data?.[0]?.tagClass).toBe("t-pen");
  });

  it("HUMANO se mantiene + marca human=true", async () => {
    const data = await runInbox([
      makeSession({ tag: "HUMANO", motivo: "Cliente pidió hablar con alguien" }),
    ]);
    expect(data?.[0]?.tag).toBe("HUMANO");
    expect(data?.[0]?.tagClass).toBe("t-human");
    expect(data?.[0]?.human).toBe(true);
    expect(data?.[0]?.handoffReason).toBe("Cliente pidió hablar con alguien");
  });

  it("active_agent_route=remarketing override → REMARKETING (aunque el tag diga otra cosa)", async () => {
    const data = await runInbox([
      makeSession({
        tag: "INTERESADO",
        active_agent_route: "remarketing",
        motivo: "remarketing programado",
      }),
    ]);
    expect(data?.[0]?.tag).toBe("REMARKETING");
    expect(data?.[0]?.tagClass).toBe("t-rem");
  });

  it("tag desconocido → fallback a PENDIENTE (no FRÍO)", async () => {
    const data = await runInbox([
      makeSession({ tag: "TAG_EXOTICO_INVENTADO" }),
    ]);
    expect(data?.[0]?.tag).toBe("PENDIENTE");
    expect(data?.[0]?.tagClass).toBe("t-pen");
  });

  it("FRÍO/FRIO (seeds prototipo o legacy) se respeta como FRÍO", async () => {
    const data = await runInbox([
      makeSession({ tag: "FRÍO" }),
      makeSession({ session_id: "wa_2", tag: "FRIO" }),
    ]);
    expect(data?.[0]?.tag).toBe("FRÍO");
    expect(data?.[1]?.tag).toBe("FRÍO");
  });
});

/* ── useChatMessages: imágenes inbound ───────────────────────────────── */

interface RawMsg {
  ui_type: string;
  role: string;
  content: string | null;
  image_url?: string;
  timestamp?: string | number;
}

function mockSessionDetail(messages: RawMsg[]) {
  globalThis.fetch = (() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          session_id: "wa_x",
          phone_number: "x",
          tag: "HUMANO",
          motivo: "",
          memory_content: null,
          active_agent_route: "humano",
          phone_number_id: null,
          pending_payment_order_id: null,
          status_history: [],
          messages,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )) as unknown as typeof fetch;
}

async function runMessages(messages: RawMsg[]) {
  mockSessionDetail(messages);
  const { result } = renderHook(() => useChatMessages("wa_x"), { wrapper: wrap });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 10));
  return result.current.data;
}

describe("useChatMessages — imagen inbound (comprobantes de pago)", () => {
  it("mapea image_url relativa del backend → imageUrl absoluta en el bubble", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "[el cliente envió un comprobante de pago: Nequi $34.000]",
        image_url: "/api/dashboard/media/wa_x/receipt_1.jpg",
      },
    ]);
    const bubble = data?.find((m) => m.kind === "in");
    // env.apiUrl en tests = http://localhost:8000 (vitest.config.ts).
    expect(bubble?.imageUrl).toBe(
      "http://localhost:8000/api/dashboard/media/wa_x/receipt_1.jpg",
    );
  });

  it("mensaje de texto sin imagen → imageUrl undefined", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "hola, info?" },
    ]);
    const bubble = data?.find((m) => m.kind === "in");
    expect(bubble?.imageUrl).toBeUndefined();
  });
});
