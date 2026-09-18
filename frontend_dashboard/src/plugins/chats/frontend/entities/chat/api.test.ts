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
import type { ChatSession } from "@plugins/chats/frontend/entities/session";
import type { ChatEvent } from "@plugins/chats/frontend/entities/message";
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
    order_ref: null,
    last_updated_timestamp: 1716700000,
    last_inbound_ms: null,
    origin: null,
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

describe("lastInboundMs (sonido de mensaje nuevo)", () => {
  it("expone el último inbound del cliente que manda el backend", async () => {
    const data = await runInbox([makeSession({ last_inbound_ms: 1_789_000_060_000 })]);
    expect(data?.[0]?.lastInboundMs).toBe(1_789_000_060_000);
  });

  it("snapshot viejo sin el campo → null (rollout tolerante)", async () => {
    const legacy: Partial<ChatSession> = makeSession();
    delete legacy.last_inbound_ms;
    const data = await runInbox([legacy as ChatSession]);
    expect(data?.[0]?.lastInboundMs).toBeNull();
  });
});

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
  sender?: "human";
  image_url?: string;
  document_url?: string;
  document_filename?: string;
  timestamp?: string | number;
  wamid?: string;
  reply_to?: { id: string; author?: string; text?: string; image_url?: string };
  /** Forma real del mensaje que proyecta el backend (ver `chatEventSchema`). */
  event?: ChatEvent;
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

describe("useChatMessages — envíos no-textuales del bot (ui_component_sent)", () => {
  it("marker de envío no-textual → nota de sistema (kind system) visible", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "quiero ver velas" },
      {
        ui_type: "ui_component_sent",
        role: "assistant",
        content: "🛍️ El bot envió el catálogo con 6 productos",
        timestamp: "2026-07-07T12:00:00+00:00",
      },
    ]);
    const note = data?.find((m) => m.kind === "system");
    expect(note).toBeDefined();
    expect(note?.text).toBe("🛍️ El bot envió el catálogo con 6 productos");
    // El mensaje del cliente sigue presente — la nota no rompe el parse
    // de la conversación completa (Zod enum cerrado).
    expect(data?.some((m) => m.kind === "in")).toBe(true);
  });

  it("formulario de datos (flow) → nota de sistema, no burbuja del bot", async () => {
    const data = await runMessages([
      {
        ui_type: "ui_component_sent",
        role: "assistant",
        content: "📋 El bot pidió los datos de envío (formulario)",
      },
    ]);
    expect(data?.find((m) => m.kind === "out")).toBeUndefined();
    expect(data?.find((m) => m.kind === "system")?.text).toContain(
      "datos de envío",
    );
  });
});

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

describe("useChatMessages — documento PDF (comprobantes de pago)", () => {
  it("mapea document_url + document_filename → documentUrl absoluta + documentName", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "[el cliente envió un documento PDF: comprobante.pdf]",
        document_url: "/api/dashboard/media/wa_x/doc-9.pdf",
        document_filename: "comprobante.pdf",
      },
    ]);
    const bubble = data?.find((m) => m.kind === "in");
    expect(bubble?.documentUrl).toBe(
      "http://localhost:8000/api/dashboard/media/wa_x/doc-9.pdf",
    );
    expect(bubble?.documentName).toBe("comprobante.pdf");
  });

  it("PDF saliente del operador → burbuja out (human) con documentUrl", async () => {
    const data = await runMessages([
      {
        ui_type: "human_message",
        role: "assistant",
        sender: "human",
        content: "Ahí va el comprobante",
        document_url: "/api/dashboard/media/wa_x/out-1.pdf",
        document_filename: "recibo.pdf",
      },
    ]);
    const bubble = data?.find((m) => m.kind === "out");
    expect(bubble?.author).toBe("human");
    expect(bubble?.documentUrl).toBe(
      "http://localhost:8000/api/dashboard/media/wa_x/out-1.pdf",
    );
    expect(bubble?.documentName).toBe("recibo.pdf");
  });

  it("mensaje sin documento → documentUrl/documentName undefined", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "hola" },
    ]);
    const bubble = data?.find((m) => m.kind === "in");
    expect(bubble?.documentUrl).toBeUndefined();
    expect(bubble?.documentName).toBeUndefined();
  });
});

describe("useChatMessages — media URLs llevan el token de auth por query", () => {
  it("con sesión activa, imageUrl y documentUrl llevan ?access_token=", async () => {
    // PM-01 del premortem: <img src> y <a target=_blank> no pueden llevar el
    // header Bearer — en prod (Cognito) el GET /media daba 401. El backend
    // acepta el JWT por query (mismo patrón que el SSE).
    const { setAccessToken } = await import("@/shared/config");
    setAccessToken("tok-123");
    try {
      const data = await runMessages([
        {
          ui_type: "user_message",
          role: "user",
          content: "foto",
          image_url: "/api/dashboard/media/wa_x/r.jpg",
        },
        {
          ui_type: "user_message",
          role: "user",
          content: "pdf",
          document_url: "/api/dashboard/media/wa_x/d.pdf",
          document_filename: "d.pdf",
        },
      ]);
      const withImg = data?.find((m) => m.imageUrl);
      const withDoc = data?.find((m) => m.documentUrl);
      expect(withImg?.imageUrl).toBe(
        "http://localhost:8000/api/dashboard/media/wa_x/r.jpg?access_token=tok-123",
      );
      expect(withDoc?.documentUrl).toBe(
        "http://localhost:8000/api/dashboard/media/wa_x/d.pdf?access_token=tok-123",
      );
    } finally {
      setAccessToken(null);
    }
  });

  it("sin token (local sin Cognito) la URL queda limpia", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "foto",
        image_url: "/api/dashboard/media/wa_x/r.jpg",
      },
    ]);
    expect(data?.find((m) => m.imageUrl)?.imageUrl).toBe(
      "http://localhost:8000/api/dashboard/media/wa_x/r.jpg",
    );
  });
});

describe("useChatInbox — 'asignada al humano' se deriva de la RUTA, no del tag (bug bandeja Humano)", () => {
  // Caso real prod 2026-09-08 (wa_573229041190): el humano confirmó el pago
  // desde Orders → `confirm_payment` deja tag=COMPRA_EXITOSA y NO toca
  // active_route (queda "humano": el bot sigue en pausa y el operador
  // responde a mano). La bandeja la mostraba como CLIENTE y desaparecía del
  // filtro "Asignadas al humano" aunque el header del chat dijera
  // "Intervenido · bot en pausa".
  it("route=humano + tag COMPRA_EXITOSA → human=true (sigue en la bandeja Humano)", async () => {
    const data = await runInbox([
      makeSession({
        tag: "COMPRA_EXITOSA",
        active_agent_route: "humano",
        motivo: "Pago verificado por human desde dashboard de orders",
      }),
    ]);
    expect(data?.[0]?.human).toBe(true);
    expect(data?.[0]?.handoffReason).toBe(
      "Pago verificado por human desde dashboard de orders",
    );
    // El tag comercial se conserva: es un cliente real, además intervenido.
    expect(data?.[0]?.tag).toBe("CLIENTE");
  });

  it("route=humano + tag CONFIRMADO_PAGO_PENDIENTE (LLM etiquetó DESPUÉS de escalar) → human=true", async () => {
    const data = await runInbox([
      makeSession({ tag: "CONFIRMADO_PAGO_PENDIENTE", active_agent_route: "humano" }),
    ]);
    expect(data?.[0]?.human).toBe(true);
    expect(data?.[0]?.tag).toBe("PENDIENTE");
  });

  it("route=ventas + tag INTERESADO → human=false (el bot la maneja)", async () => {
    const data = await runInbox([makeSession()]);
    expect(data?.[0]?.human).toBe(false);
    expect(data?.[0]?.handoffReason).toBeUndefined();
  });
});

describe("useChatMessages — reply del cliente (cita de un mensaje)", () => {
  it("reply a una foto del bot → replyTo con autor, texto e imagen", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "que el velón amor eterno sea este",
        wamid: "wamid.in",
        reply_to: {
          id: "wamid.bot",
          author: "agent",
          text: "Velón Amor Eterno",
          image_url: "https://assets.hubara.com.co/amor-eterno.webp",
        },
      },
    ]);
    const bubble = data?.find((m) => m.kind === "in");
    expect(bubble?.replyTo).toEqual({
      author: "agent",
      text: "Velón Amor Eterno",
      imageUrl: "https://assets.hubara.com.co/amor-eterno.webp",
    });
  });

  it("reply a una foto del cliente → imagen relativa absolutizada", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "que sea este",
        reply_to: {
          id: "wamid.photo",
          author: "user",
          text: "[el cliente envió una foto]",
          image_url: "/api/dashboard/media/wa_x/1.jpg",
        },
      },
    ]);
    expect(data?.find((m) => m.kind === "in")?.replyTo?.imageUrl).toBe(
      "http://localhost:8000/api/dashboard/media/wa_x/1.jpg",
    );
  });

  it("cita no resuelta → replyTo con autor desconocido y sin contenido", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: "y esa?",
        reply_to: { id: "wamid.gone" },
      },
    ]);
    expect(data?.find((m) => m.kind === "in")?.replyTo).toEqual({
      author: "unknown",
      text: undefined,
      imageUrl: undefined,
    });
  });

  it("mensaje sin cita → replyTo undefined", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "hola" },
    ]);
    expect(data?.find((m) => m.kind === "in")?.replyTo).toBeUndefined();
  });
});

describe("useChatMessages — el token NUNCA viaja a un origen externo", () => {
  it("PM-01 (reply): imagen citada del CDN (otro origen) queda sin ?access_token=", async () => {
    // Las fotos del bot viven en assets.hubara.com.co: appendear el JWT ahí
    // lo filtraría a un tercero. Solo las URLs del API llevan el token.
    const { setAccessToken } = await import("@/shared/config");
    setAccessToken("tok-123");
    try {
      const data = await runMessages([
        {
          ui_type: "user_message",
          role: "user",
          content: "este",
          reply_to: {
            id: "wamid.bot",
            author: "agent",
            text: "Velón",
            image_url: "https://assets.hubara.com.co/velon.webp",
          },
        },
        {
          ui_type: "user_message",
          role: "user",
          content: "y esta",
          reply_to: {
            id: "wamid.me",
            author: "user",
            image_url: "/api/dashboard/media/wa_x/1.jpg",
          },
        },
      ]);
      const [cdn, own] = (data ?? []).filter((m) => m.kind === "in");
      expect(cdn?.replyTo?.imageUrl).toBe("https://assets.hubara.com.co/velon.webp");
      expect(own?.replyTo?.imageUrl).toBe(
        "http://localhost:8000/api/dashboard/media/wa_x/1.jpg?access_token=tok-123",
      );
    } finally {
      setAccessToken(null);
    }
  });
});

/**
 * Chip de pedido en la fila de la bandeja: el operador no distinguía una
 * conversación que YA se convirtió en pedido de una que sigue negociando —
 * ambas se ven igual en el filtro "Asignadas al humano" (un chat queda en
 * manos del humano por muchos motivos después de cerrar la venta).
 *
 * El backend manda `order_ref` en el mismo snapshot de sesión (sale del
 * metadata del vault, sin llamar a Medusa por fila).
 */
describe("order (chip de pedido en la fila)", () => {
  const ORDER_ID = "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z";

  it("expone el número humano del pedido y el estado del pago", async () => {
    const data = await runInbox([
      makeSession({
        tag: "HUMANO",
        active_agent_route: "humano",
        order_ref: {
          order_id: ORDER_ID,
          display_id: "31",
          payment: "pending",
          count: 1,
        },
      }),
    ]);
    expect(data?.[0]?.order).toEqual({
      label: "#31",
      orderId: ORDER_ID,
      payment: "pending",
      count: 1,
    });
  });

  it("sin pedido registrado → null (la fila no pinta chip)", async () => {
    const data = await runInbox([makeSession({ order_ref: null })]);
    expect(data?.[0]?.order).toBeNull();
  });

  it("snapshot viejo sin el campo → null (rollout tolerante)", async () => {
    const legacy: Partial<ChatSession> = makeSession();
    delete legacy.order_ref;
    const data = await runInbox([legacy as ChatSession]);
    expect(data?.[0]?.order).toBeNull();
  });

  it("sin número de orden no hay chip — nunca el id interno de Medusa", async () => {
    // La primera versión caía a "…B3XY9Z": un id que al operador no le sirve.
    const data = await runInbox([
      makeSession({
        order_ref: {
          order_id: ORDER_ID,
          display_id: null,
          payment: "confirmed",
          count: 1,
        },
      }),
    ]);
    expect(data?.[0]?.order).toBeNull();
  });

  it("un solo # aunque el backend mande el número ya formateado", async () => {
    // Frontend y backend despliegan por separado: un backend viejo manda
    // "#32" (formato de la vista Orders) y el chip pintaba "##32".
    const data = await runInbox([
      makeSession({
        order_ref: {
          order_id: ORDER_ID,
          display_id: "#32",
          payment: "pending",
          count: 1,
        },
      }),
    ]);
    expect(data?.[0]?.order?.label).toBe("#32");
  });
});

/**
 * Eventos estructurados: el backend proyecta cada marker del historial a su
 * forma real (`event`) para que el panel pinte botones como botones y separe
 * lo que escribió la persona de lo que describió la IA.
 */
describe("useChatMessages — eventos del hilo", () => {
  it("los botones del bot dejan de ser una nota de sistema: son su mensaje", async () => {
    const data = await runMessages([
      {
        ui_type: "ui_component_sent",
        role: "assistant",
        content: "🔘 El bot envió botones: Ver catálogo · Asesoría — con el mensaje: «Buenas tardes.»",
        timestamp: "2026-09-17T18:17:00+00:00",
        event: {
          kind: "bot_buttons",
          body: "Buenas tardes.",
          buttons: [{ title: "Ver catálogo", touched: true }, { title: "Asesoría" }],
        },
      },
    ]);
    const msg = data?.[data.length - 1];
    expect(msg?.kind).toBe("out");
    expect(msg?.author).toBe("bot");
    expect(msg?.event).toEqual({
      kind: "bot_buttons",
      body: "Buenas tardes.",
      buttons: [{ title: "Ver catálogo", touched: true }, { title: "Asesoría" }],
    });
  });

  it("el resto de envíos no-textuales sigue siendo nota de sistema", async () => {
    const data = await runMessages([
      {
        ui_type: "ui_component_sent",
        role: "assistant",
        content: "🛍️ El bot envió el catálogo con 6 productos",
        timestamp: "2026-09-17T18:17:00+00:00",
      },
    ]);
    expect(data?.find((m) => m.kind === "system")).toBeDefined();
  });

  it("la foto del cliente llega con caption y visión separados", async () => {
    const data = await runMessages([
      {
        ui_type: "user_message",
        role: "user",
        content: '[el cliente envió una foto: Vela verde.] con el texto: "Precio?"',
        image_url: "/api/dashboard/media/wa_x/1.jpg",
        event: {
          kind: "customer_photo",
          vision: "Vela verde.",
          caption: "Precio?",
          receipt: false,
        },
      },
    ]);
    const msg = data?.find((m) => m.kind === "in");
    expect(msg?.event).toEqual({
      kind: "customer_photo",
      vision: "Vela verde.",
      caption: "Precio?",
      receipt: false,
    });
  });

  it("mensaje sin evento → sin `event` (se pinta como siempre)", async () => {
    const data = await runMessages([
      { ui_type: "user_message", role: "user", content: "Hola" },
    ]);
    expect(data?.find((m) => m.kind === "in")?.event).toBeUndefined();
  });
});
