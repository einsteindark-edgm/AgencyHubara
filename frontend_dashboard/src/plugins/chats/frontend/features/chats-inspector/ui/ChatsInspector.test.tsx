import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatsInspector } from "./ChatsInspector";

const overviewMock = vi.fn<(id: string | null) => { data: unknown }>();
const mutateMock = vi.fn();

vi.mock("@plugins/chats/frontend/entities/chat", () => ({
  useChatInbox: vi.fn(() => ({ data: [] })),
  useChatMemory: vi.fn(() => ({ data: [] })),
  useChatRoutingLog: vi.fn(() => ({ data: [] })),
  useChatOverview: (id: string | null) => overviewMock(id),
}));

vi.mock("@plugins/chats/frontend/entities/session-tag", () => ({
  OPERATOR_TAGS: ["INTERESADO", "RECHAZO", "REMARKETING"],
  OPERATOR_TAG_LABELS: {
    INTERESADO: "Interesado",
    RECHAZO: "Rechazo",
    REMARKETING: "Remarketing",
  },
  useReassignTagMutation: vi.fn(() => ({
    mutate: mutateMock,
    isPending: false,
    isError: false,
    error: null,
  })),
}));

beforeEach(() => {
  overviewMock.mockReset();
  overviewMock.mockReturnValue({ data: null });
  mutateMock.mockReset();
});

describe("ChatsInspector — Estado actual con datos reales (origen + reasignar)", () => {
  it("muestra el origen real de la campaña y la fecha de inicio", () => {
    overviewMock.mockReturnValue({
      data: {
        sessionId: "wa_573114842180",
        tag: "INTERESADO",
        startedLabel: "09/09, 21:26",
        originLabel: "Meta Ads · Día del Padre",
        originDetail: "Velas aromáticas",
        originIsMeta: true,
      },
    });
    render(<ChatsInspector chatId="wa_573114842180" />);
    expect(screen.getByText("wa_573114842180")).toBeInTheDocument();
    expect(screen.getByText("09/09, 21:26")).toBeInTheDocument();
    expect(screen.getByText("Meta Ads · Día del Padre")).toBeInTheDocument();
    expect(screen.getByText("Velas aromáticas")).toBeInTheDocument();
    // nada del prototipo hardcodeado
    expect(screen.queryByText(/ses_a3f8c2/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Meta Ads · velas/)).not.toBeInTheDocument();
  });

  it("Reasignar abre el formulario y envía tag + motivo al backend", async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="wa_573114842180" />);
    await user.click(screen.getByText(/Reasignar/));
    await user.selectOptions(screen.getByLabelText("Nuevo tag"), "RECHAZO");
    await user.type(screen.getByLabelText("Motivo"), "buscaba cera, no la vendemos");
    await user.click(screen.getByText("Guardar"));
    expect(mutateMock).toHaveBeenCalledTimes(1);
    expect(mutateMock.mock.calls[0][0]).toEqual({
      tag: "RECHAZO",
      motivo: "buscaba cera, no la vendemos",
    });
  });

  it("Guardar queda deshabilitado sin motivo", async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="wa_573114842180" />);
    await user.click(screen.getByText(/Reasignar/));
    expect(screen.getByText("Guardar")).toBeDisabled();
    await user.click(screen.getByText("Cancelar"));
    expect(screen.getByText(/Reasignar/)).toBeInTheDocument();
    expect(mutateMock).not.toHaveBeenCalled();
  });
});

describe("ChatsInspector — panel derecho simplificado", () => {
  it('no muestra el campo "Mensajes" en tab Estado actual', () => {
    render(<ChatsInspector chatId="test-chat-id" />);
    expect(screen.queryByText("Mensajes")).not.toBeInTheDocument();
  });

  it('no muestra el campo "Sentimiento" en tab Estado actual', () => {
    render(<ChatsInspector chatId="test-chat-id" />);
    expect(screen.queryByText("Sentimiento")).not.toBeInTheDocument();
  });

  it('no muestra el botón "Cambiar tag" en ningún tab', () => {
    render(<ChatsInspector chatId="test-chat-id" />);
    expect(screen.queryByText(/Cambiar tag/)).not.toBeInTheDocument();
  });

  it('no muestra el botón "Cerrar" en tab Estado actual', () => {
    render(<ChatsInspector chatId="test-chat-id" />);
    expect(screen.queryByText(/Cerrar/)).not.toBeInTheDocument();
  });

  it('sí muestra el botón "Reasignar" en tab Estado actual', () => {
    render(<ChatsInspector chatId="test-chat-id" />);
    expect(screen.getByText(/Reasignar/)).toBeInTheDocument();
  });

  it('no muestra el campo "Temperatura" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Temperatura")).not.toBeInTheDocument();
  });

  it('no muestra el campo "Tokens" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Tokens")).not.toBeInTheDocument();
  });

  it('no muestra el botón "Prompt" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Prompt")).not.toBeInTheDocument();
  });

  it('no muestra el botón "Flujo" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Flujo")).not.toBeInTheDocument();
  });

  it('no muestra el botón "Probar" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Probar")).not.toBeInTheDocument();
  });

  it('no muestra el botón "Clonar" en tab Agente', async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId="test-chat-id" />);
    await user.click(screen.getByTitle("Agente actual"));
    expect(screen.queryByText("Clonar")).not.toBeInTheDocument();
  });
});
