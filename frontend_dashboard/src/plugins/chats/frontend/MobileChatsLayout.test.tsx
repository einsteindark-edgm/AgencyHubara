/**
 * Test de navegación del shell móvil de chats: inbox → tap en un chat →
 * conversación (con back) → volver. Mockeamos las 3 features y los hooks de
 * datos para aislar la lógica de navegación de una columna.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useState, type ReactNode } from "react";
import { PluginHostProvider } from "@/shared/lib";
import { MobileChatsLayout } from "./MobileChatsLayout";

// ── Mocks de features ──────────────────────────────────────────────────────
vi.mock("@plugins/chats/frontend/features/chats-inbox", () => ({
  ChatsInbox: ({ onSelect }: { onSelect: (id: string) => void }) => (
    <button onClick={() => onSelect("wa_42")}>Abrir chat wa_42</button>
  ),
  useHandoffNotifications: () => undefined,
}));
vi.mock("@plugins/chats/frontend/features/chats-conversation", () => ({
  ChatsConversation: ({ chatId }: { chatId: string | null }) => (
    <div data-testid="conversation">Conversación de {chatId}</div>
  ),
}));
vi.mock("@plugins/chats/frontend/features/chats-inspector", () => ({
  ChatsInspector: ({ chatId }: { chatId: string | null }) => (
    <div data-testid="inspector">Inspector de {chatId}</div>
  ),
}));

// ── Mocks de datos ─────────────────────────────────────────────────────────
vi.mock("@plugins/chats/frontend/entities/chat", () => ({
  useChatInbox: () => ({
    data: [{ id: "wa_42", name: "Cliente 42" }],
  }),
  useSessionsStream: () => undefined,
}));
vi.mock("@/shared/api", () => ({
  useInvalidateOnReconnect: () => undefined,
}));

function Wrapper({ children }: { children: ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  // Host CON estado real: `useSelection` lee/escribe acá, igual que el
  // PluginHostProvider de la app real. Con un setSelection no-op el gate de
  // vista (que depende de selectedChatId) nunca cambiaría.
  const [selection, setSelectionMap] = useState<Record<string, string | null>>(
    {},
  );
  const setSelection = useCallback((key: string, id: string | null) => {
    setSelectionMap((prev) => (prev[key] === id ? prev : { ...prev, [key]: id }));
  }, []);
  const host = { showSidebar: true, showInspector: true, selection, setSelection };
  return (
    <QueryClientProvider client={client}>
      <PluginHostProvider value={host}>{children}</PluginHostProvider>
    </QueryClientProvider>
  );
}

describe("MobileChatsLayout", () => {
  it("arranca en la bandeja (inbox), no en una conversación", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    expect(screen.getByText("Chats")).toBeInTheDocument();
    expect(screen.getByText("Abrir chat wa_42")).toBeInTheDocument();
    expect(screen.queryByTestId("conversation")).not.toBeInTheDocument();
  });

  it("tap en un chat abre la conversación con botón atrás", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    expect(screen.getByTestId("conversation")).toHaveTextContent("wa_42");
    expect(
      screen.getByRole("button", { name: /volver a la bandeja/i }),
    ).toBeInTheDocument();
  });

  it("el botón atrás vuelve a la bandeja", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    fireEvent.click(
      screen.getByRole("button", { name: /volver a la bandeja/i }),
    );
    expect(screen.getByText("Abrir chat wa_42")).toBeInTheDocument();
    expect(screen.queryByTestId("conversation")).not.toBeInTheDocument();
  });

  it("el toggle de detalles abre el inspector como bottom-sheet", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    expect(screen.queryByTestId("inspector")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /detalles del contacto/i }),
    );
    expect(screen.getByTestId("inspector")).toHaveTextContent("wa_42");
  });
});
