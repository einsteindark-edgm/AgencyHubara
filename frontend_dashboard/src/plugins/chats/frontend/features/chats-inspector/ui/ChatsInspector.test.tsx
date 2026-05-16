import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatsInspector } from "./ChatsInspector";

vi.mock("@/entities/chat", () => ({
  useChatInbox: vi.fn(() => ({ data: [] })),
  useChatMemory: vi.fn(() => ({ data: [] })),
  useChatRoutingLog: vi.fn(() => ({ data: [] })),
}));

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
