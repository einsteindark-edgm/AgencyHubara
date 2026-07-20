/**
 * Comportamiento del visor de audiencia: modal accesible (dialog + Escape +
 * click-afuera), burbujas por rol estilo Chats (user izq / assistant der),
 * badge Template, empty state sin historial y CURADURÍA manual (quitar /
 * restaurar / agregar número → PUT con las listas REPLACE completas).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import { ApiError } from "@/shared/sdk";

import type {
  AudienceConversation,
  AudienceRecipient,
  SkippedContact,
} from "@plugins/marketing/frontend/entities/audience";
import type { Campaign } from "@plugins/marketing/frontend/entities/campaign";

const conversationMock = {
  data: undefined as AudienceConversation | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/audience", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useAudienceConversation: () => conversationMock,
}));

/** El viewer instancia DOS mutations (curaduría de filas + alta de número),
 *  en ese orden de hooks — la paridad del contador las separa. */
const curateMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
};
const addMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
};
let updateHookCalls = 0;

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useUpdateCampaign: () => (updateHookCalls++ % 2 === 0 ? curateMock : addMock),
}));

import { AudienceViewer } from "./ui/AudienceViewer";

const RECIPIENTS: AudienceRecipient[] = [
  {
    sessionId: "wa_573001234567",
    phone: "+573001234567",
    customerName: "Camila",
    segment: "clientes",
  },
  {
    sessionId: "wa_573007654321",
    phone: "+573007654321",
    customerName: null,
    segment: "frios",
  },
];

function makeCampaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: "mkt-1",
    name: "Día del padre",
    status: "draft",
    goal: "discount_general",
    percent: 20,
    couponCode: "PAPA20",
    validUntil: "15 de junio",
    productHandle: null,
    segments: ["clientes", "frios"],
    message: { header: "H", body: "B", footer: "", cta: "C" },
    templateName: "campaign_promo_marketing_v1",
    scheduleAtMs: null,
    createdAtMs: 1,
    updatedAtMs: 1,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
    excludedSessionIds: [],
    extraSessionIds: [],
    ...over,
  };
}

function renderViewer(
  over: {
    sessionId?: string;
    campaign?: Campaign;
    recipients?: AudienceRecipient[];
    removed?: SkippedContact[];
    total?: number;
  } = {},
) {
  const onClose = vi.fn();
  const onSelectSession = vi.fn();
  const utils = render(
    <AudienceViewer
      campaign={over.campaign ?? makeCampaign()}
      recipients={over.recipients ?? RECIPIENTS}
      removed={over.removed ?? []}
      total={over.total ?? RECIPIENTS.length}
      sessionId={over.sessionId ?? "wa_573001234567"}
      onSelectSession={onSelectSession}
      onClose={onClose}
    />,
  );
  return { ...utils, onClose, onSelectSession };
}

beforeEach(() => {
  conversationMock.data = undefined;
  conversationMock.isPending = false;
  conversationMock.error = null;
  curateMock.mutate.mockClear();
  curateMock.error = null;
  addMock.mutate.mockClear();
  addMock.error = null;
  updateHookCalls = 0;
});

describe("AudienceViewer — modal", () => {
  it("es un dialog accesible con el teléfono y nombre en el header", () => {
    conversationMock.data = { sessionId: "wa_573001234567", messages: [] };
    const { getByRole, getAllByText } = renderViewer();
    const dialog = getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // Teléfono en header (además de la fila del panel izquierdo).
    expect(getAllByText("+573001234567").length).toBeGreaterThanOrEqual(2);
    expect(getAllByText("Camila").length).toBeGreaterThanOrEqual(1);
  });

  it("cierra con Escape y con click en el backdrop, no con click adentro", () => {
    conversationMock.data = { sessionId: "wa_573001234567", messages: [] };
    const { getByRole, getByTestId, onClose } = renderViewer();
    fireEvent.click(getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(getByTestId("audience-viewer-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("click en otra fila de la lista selecciona esa sesión", () => {
    conversationMock.data = { sessionId: "wa_573001234567", messages: [] };
    const { getByRole, onSelectSession } = renderViewer();
    // La fila (no el "×" de quitar): su accessible name incluye el chip.
    fireEvent.click(
      getByRole("button", {
        name: (n) => n.includes("+573007654321") && n.includes("Fríos"),
      }),
    );
    expect(onSelectSession).toHaveBeenCalledWith("wa_573007654321");
  });
});

describe("AudienceViewer — conversación", () => {
  it("pinta burbujas por rol: user a la izquierda, assistant a la derecha", () => {
    conversationMock.data = {
      sessionId: "wa_573001234567",
      messages: [
        {
          role: "user",
          kind: "text",
          content: "Hola, ¿tienen velas?",
          timestamp: "2026-07-16T14:30:00Z",
        },
        {
          role: "assistant",
          kind: "text",
          content: "¡Claro que sí!",
          timestamp: null,
        },
      ],
    };
    const { getByText } = renderViewer();
    const userBubble = getByText("Hola, ¿tienen velas?").closest("div");
    const botBubble = getByText("¡Claro que sí!").closest("div");
    expect(userBubble?.className).toContain("self-start");
    expect(userBubble?.className).toContain("bg-bubble-out");
    expect(botBubble?.className).toContain("self-end");
    expect(botBubble?.className).toContain("bg-bubble-in");
    // Timestamp atenuado cuando viene.
    expect(getByText("2026-07-16T14:30:00Z")).toBeTruthy();
  });

  it("kind template: burbuja out con badge Template", () => {
    conversationMock.data = {
      sessionId: "wa_573001234567",
      messages: [
        {
          role: "assistant",
          kind: "template",
          content: "¡Hola Camila! 20% off…",
          timestamp: null,
        },
      ],
    };
    const { getByText } = renderViewer();
    expect(getByText("Template")).toBeTruthy();
    const bubble = getByText("¡Hola Camila! 20% off…").closest("div");
    expect(bubble?.className).toContain("self-end");
  });

  it("sin mensajes muestra el empty state", () => {
    conversationMock.data = { sessionId: "wa_573001234567", messages: [] };
    const { getByText } = renderViewer();
    expect(getByText("Sin historial de conversación")).toBeTruthy();
  });
});

describe("AudienceViewer — curaduría", () => {
  it("quitar un destinatario manda PUT con excluded_session_ids + la sesión", () => {
    const { getByRole } = renderViewer({
      campaign: makeCampaign({ excludedSessionIds: ["wa_previo"] }),
    });
    fireEvent.click(getByRole("button", { name: "Quitar +573007654321" }));
    expect(curateMock.mutate).toHaveBeenCalledTimes(1);
    expect(curateMock.mutate.mock.calls[0]?.[0]).toEqual({
      excludedSessionIds: ["wa_previo", "wa_573007654321"],
    });
  });

  it("un agregado manual lleva chip Manual y su × lo saca de extra_session_ids", () => {
    const manual: AudienceRecipient = {
      sessionId: "wa_+573009998877",
      phone: "+573009998877",
      customerName: null,
      segment: "manual",
    };
    const { getByText, getByRole } = renderViewer({
      recipients: [...RECIPIENTS, manual],
      campaign: makeCampaign({ extraSessionIds: ["wa_+573009998877"] }),
    });
    expect(getByText("Manual")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Quitar +573009998877" }));
    expect(curateMock.mutate).toHaveBeenCalledTimes(1);
    expect(curateMock.mutate.mock.calls[0]?.[0]).toEqual({
      extraSessionIds: [],
    });
  });

  it("Restaurar un quitado lo saca de excluded_session_ids", () => {
    const { getByText, getByRole } = renderViewer({
      campaign: makeCampaign({ excludedSessionIds: ["wa_573001111111"] }),
      removed: [
        {
          sessionId: "wa_573001111111",
          phone: "+573001111111",
          reason: "quitado_por_operador",
        },
      ],
    });
    expect(getByText("Quitados por vos (1)")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: /Restaurar/ }));
    expect(curateMock.mutate).toHaveBeenCalledTimes(1);
    expect(curateMock.mutate.mock.calls[0]?.[0]).toEqual({
      excludedSessionIds: [],
    });
  });

  it("agregar normaliza el teléfono y manda extra_session_ids con wa_+…", () => {
    const { getByPlaceholderText, getByRole } = renderViewer();
    fireEvent.change(getByPlaceholderText("+57 300 123 4567"), {
      target: { value: "+57 300 999-88(77)" },
    });
    fireEvent.click(getByRole("button", { name: "Agregar" }));
    expect(addMock.mutate).toHaveBeenCalledTimes(1);
    expect(addMock.mutate.mock.calls[0]?.[0]).toEqual({
      extraSessionIds: ["wa_+573009998877"],
    });
  });

  it("Enter en el input también agrega", () => {
    const { getByPlaceholderText } = renderViewer();
    const input = getByPlaceholderText("+57 300 123 4567");
    fireEvent.change(input, { target: { value: "+57 300 123 4567" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(addMock.mutate).toHaveBeenCalledTimes(1);
    expect(addMock.mutate.mock.calls[0]?.[0]).toEqual({
      extraSessionIds: ["wa_+573001234567"],
    });
  });

  it("superficie el detail del 422 (sin conversación previa) bajo el input", () => {
    addMock.error = new ApiError(422, {
      detail:
        "Sin conversación previa con el bot: wa_+573009998877 — el número debe haber chateado al menos una vez",
    });
    const { getByText } = renderViewer();
    expect(getByText(/Sin conversación previa con el bot/)).toBeTruthy();
  });

  it("footer: total confirmado + Confirmar audiencia cierra el modal", () => {
    const { getByText, getByRole, onClose } = renderViewer({ total: 2 });
    expect(getByText(/2 destinatarios confirmados/)).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Confirmar audiencia" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("campaña enviada: read-only — sin ×, sin input, sin Restaurar", () => {
    const { queryByRole, queryByPlaceholderText } = renderViewer({
      campaign: makeCampaign({ status: "sent" }),
      removed: [
        {
          sessionId: "wa_573001111111",
          phone: "+573001111111",
          reason: "quitado_por_operador",
        },
      ],
    });
    expect(queryByRole("button", { name: /Quitar/ })).toBeNull();
    expect(queryByRole("button", { name: /Restaurar/ })).toBeNull();
    expect(queryByPlaceholderText("+57 300 123 4567")).toBeNull();
  });
});
