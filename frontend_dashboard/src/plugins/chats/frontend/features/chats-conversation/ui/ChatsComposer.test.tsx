/**
 * Smoke test del composer cableado al handoff real.
 *
 * Verificamos los dos modos:
 *  - `active_agent_route !== "humano"` → banner "bot gestionando" + botón Intervenir.
 *  - `active_agent_route === "humano"` → textarea + botón Devolver al bot.
 *
 * `useSession` lo mockeamos directo en el módulo `@/entities/session` para no
 * tener que cablear fetch.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ChatsComposer } from "./ChatsComposer";

const useSessionMock = vi.fn();

vi.mock("@/entities/session", async () => {
  const actual = await vi.importActual<typeof import("@/entities/session")>(
    "@/entities/session",
  );
  return {
    ...actual,
    useSession: (id: string | null) => useSessionMock(id),
  };
});

const interveneMutate = vi.fn();
const sendMutate = vi.fn();
const returnMutate = vi.fn();

vi.mock("@/entities/handoff", () => ({
  useInterveneMutation: () => ({
    mutate: interveneMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useSendHumanMessageMutation: () => ({
    mutate: sendMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useReturnToBotMutation: () => ({
    mutate: returnMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  useSessionMock.mockReset();
  interveneMutate.mockReset();
  sendMutate.mockReset();
  returnMutate.mockReset();
});

describe("ChatsComposer", () => {
  it("renders 'bot managing' banner when route is ventas", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "ventas" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(
      screen.getByText(/está gestionando esta conversación/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /intervenir/i }),
    ).toBeInTheDocument();
  });

  it("clicking Intervenir calls intervene mutation", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "ventas" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByRole("button", { name: /intervenir/i }));
    expect(interveneMutate).toHaveBeenCalledWith({});
  });

  it("renders textarea + return-to-bot when route is humano", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(
      screen.getByPlaceholderText(/Escribe un mensaje al cliente/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Devolver al bot/i)).toBeInTheDocument();
  });

  it("typing + send calls sendMessage mutation with text", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    const textarea = screen.getByPlaceholderText(
      /Escribe un mensaje al cliente/i,
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "hola humano" } });
    // El send button no tiene texto visible; encontramos por title.
    const sendBtn = screen.getByTitle(/Enviar/i);
    fireEvent.click(sendBtn);
    expect(sendMutate).toHaveBeenCalled();
    const callArg = sendMutate.mock.calls[0][0];
    expect(callArg).toEqual({ text: "hola humano" });
  });

  it("clicking Devolver al bot opens picker modal", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    expect(screen.getByText(/A qué bot quieres devolver/i)).toBeInTheDocument();
    expect(screen.getByText(/Sales/)).toBeInTheDocument();
    expect(screen.getByText(/Remarketing/)).toBeInTheDocument();
  });

  it("picker → ventas confirm calls returnToBot with target=ventas", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    fireEvent.click(screen.getByText(/Confirmar/i));
    expect(returnMutate).toHaveBeenCalled();
    const arg = returnMutate.mock.calls[0][0];
    expect(arg.target_route).toBe("ventas");
  });

  it("picker → remarketing requires motivo before confirm", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    // Seleccionar Remarketing
    fireEvent.click(screen.getByLabelText(/Remarketing/i));
    // Sin motivo, Confirmar queda disabled
    const confirmBtn = screen.getByText(/Confirmar/i) as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
    // Con motivo, Confirmar habilita y dispara mutación
    const motivoBox = screen.getByPlaceholderText(/Motivo del gancho/i);
    fireEvent.change(motivoBox, {
      target: { value: "cliente indeciso, retomar suave" },
    });
    expect((screen.getByText(/Confirmar/i) as HTMLButtonElement).disabled).toBe(
      false,
    );
    fireEvent.click(screen.getByText(/Confirmar/i));
    expect(returnMutate).toHaveBeenCalled();
    const arg = returnMutate.mock.calls[0][0];
    expect(arg.target_route).toBe("remarketing");
    expect(arg.motivo).toMatch(/indeciso/);
  });
});
