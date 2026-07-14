/**
 * Test de navegación del shell móvil de chats: inbox → tap en un chat →
 * conversación (con back) → volver. Mockeamos las 3 features y los hooks de
 * datos para aislar la lógica de navegación de una columna.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
vi.mock("@plugins/chats/frontend/features/chats-orders", () => ({
  ChatsOrdersPanel: ({ sessionId }: { sessionId: string | null }) => (
    <div data-testid="orders-panel">Pedidos de {sessionId}</div>
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

  it("el botón atrás vuelve a la bandeja (consumiendo SU entrada de historial)", async () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    // PM2-M1: el cierre por UI hace history.back() — el popstate (async en
    // jsdom) es la única fuente del cambio de estado. Sin esto, cada ciclo
    // abrir/cerrar dejaba una entrada huérfana y el back físico acumulaba
    // taps muertos.
    fireEvent.click(
      screen.getByRole("button", { name: /volver a la bandeja/i }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("conversation")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Abrir chat wa_42")).toBeInTheDocument();
  });

  it("el back físico (popstate) con un sheet abierto cierra el sheet, no el chat", async () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    fireEvent.click(
      screen.getByRole("button", { name: /pedidos del cliente/i }),
    );
    expect(screen.getByTestId("orders-panel")).toBeInTheDocument();
    // Back físico de Android = popstate del WebView.
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() =>
      expect(screen.queryByTestId("orders-panel")).not.toBeInTheDocument(),
    );
    // El chat sigue abierto; un segundo back vuelve a la bandeja.
    expect(screen.getByTestId("conversation")).toBeInTheDocument();
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() =>
      expect(screen.queryByTestId("conversation")).not.toBeInTheDocument(),
    );
  });

  it("cerrar el sheet con el backdrop también consume la entrada de historial", async () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    fireEvent.click(
      screen.getByRole("button", { name: /pedidos del cliente/i }),
    );
    fireEvent.click(screen.getByRole("presentation"));
    await waitFor(() =>
      expect(screen.queryByTestId("orders-panel")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("conversation")).toBeInTheDocument();
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

  it("el botón Pedidos abre el panel de pedidos del cliente", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    expect(screen.queryByTestId("orders-panel")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /pedidos del cliente/i }),
    );
    expect(screen.getByTestId("orders-panel")).toHaveTextContent("wa_42");
  });

  it("un solo sheet a la vez: abrir Pedidos cierra el inspector", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    fireEvent.click(
      screen.getByRole("button", { name: /detalles del contacto/i }),
    );
    expect(screen.getByTestId("inspector")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /pedidos del cliente/i }),
    );
    expect(screen.queryByTestId("inspector")).not.toBeInTheDocument();
    expect(screen.getByTestId("orders-panel")).toBeInTheDocument();
  });

  it("PM2-M9: el dialog accesible es el sheet, con nombre", () => {
    render(<MobileChatsLayout />, { wrapper: Wrapper });
    fireEvent.click(screen.getByText("Abrir chat wa_42"));
    fireEvent.click(
      screen.getByRole("button", { name: /pedidos del cliente/i }),
    );
    expect(
      screen.getByRole("dialog", { name: /pedidos del cliente/i }),
    ).toBeInTheDocument();
  });
});
