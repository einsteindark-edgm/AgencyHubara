/**
 * Tests de la entity `order-ref` — read-side + coherencia de cache.
 *
 * El invariante clave: tras agendar (useScheduleOrder éxito), el detalle
 * cacheado del pedido se INVALIDA — así "Confirmar pago" ve el `due_iso`
 * recién asignado y no re-agenda por encima (el popover montado no se
 * desmonta entre una acción y la otra).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const getMock = vi.fn();
const patchMock = vi.fn();

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
  },
}));

import {
  useCustomerOrders,
  useOrderRefDetail,
  useScheduleOrder,
  useTransitionOrderStage,
} from "./api";
import { orderRefKeys } from "./keys";

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  getMock.mockReset();
  patchMock.mockReset();
});

describe("order-ref api", () => {
  it("useOrderRefDetail GETea el cast propio de chats y parsea due_iso", async () => {
    getMock.mockResolvedValue({ summary: { id: "o1", due_iso: "2026-07-15" } });
    const { result } = renderHook(() => useOrderRefDetail("o1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.summary.due_iso).toBe("2026-07-15");
    // P-9/P-23: el literal pertenece a chats — nunca al API del plugin orders.
    expect(String(getMock.mock.calls[0][0])).toBe("/api/chats/order-actions/o1");
  });

  it("PM-006: con enabled=false NO fetchea — lazy hasta que el popover abre", async () => {
    getMock.mockResolvedValue({ summary: { id: "o1", due_iso: null } });
    const { result } = renderHook(
      () => useOrderRefDetail("o1", { enabled: false }),
      { wrapper: makeWrapper() },
    );
    // Dar un tick al event loop: si el hook fuera eager, ya habría fetcheado.
    await new Promise((r) => setTimeout(r, 25));
    expect(getMock).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it("agendar con éxito refresca el detalle — Confirmar pago ve la fecha nueva", async () => {
    getMock
      .mockResolvedValueOnce({ summary: { id: "o1", due_iso: null } })
      .mockResolvedValueOnce({ summary: { id: "o1", due_iso: "2026-07-20" } });
    patchMock.mockResolvedValue({ success: true });
    const { result } = renderHook(
      () => ({ detail: useOrderRefDetail("o1"), schedule: useScheduleOrder() }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.detail.isSuccess).toBe(true));
    expect(result.current.detail.data?.summary.due_iso).toBeNull();

    await result.current.schedule.mutateAsync({
      orderId: "o1",
      delivery_iso: "2026-07-20",
    });

    await waitFor(() =>
      expect(result.current.detail.data?.summary.due_iso).toBe("2026-07-20"),
    );
  });

  // ── Panel de pedidos del cliente (mobile): listar + cambiar estado ────────

  it("useCustomerOrders GETea by-session y parsea la lista", async () => {
    getMock.mockResolvedValue({
      orders: [{ id: "order_01HX", status: "preparing", total_cop: 124500 }],
      count: 1,
    });
    const { result } = renderHook(() => useCustomerOrders("wa_573001"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.orders[0].id).toBe("order_01HX");
    expect(result.current.data?.orders[0].status).toBe("preparing");
    expect(String(getMock.mock.calls[0][0])).toBe(
      "/api/chats/order-actions/by-session/wa_573001",
    );
  });

  it("useCustomerOrders no fetchea sin sessionId", async () => {
    renderHook(() => useCustomerOrders(null), { wrapper: makeWrapper() });
    await new Promise((r) => setTimeout(r, 25));
    expect(getMock).not.toHaveBeenCalled();
  });

  it("useTransitionOrderStage PATCHea /stage y devuelve el resultado", async () => {
    patchMock.mockResolvedValue({ success: true, current_stage: "ready" });
    const { result } = renderHook(
      () => useTransitionOrderStage("wa_573001"),
      { wrapper: makeWrapper() },
    );
    const data = await result.current.mutateAsync({
      orderId: "order_01HX",
      stage: "ready",
    });
    expect(data.success).toBe(true);
    expect(String(patchMock.mock.calls[0][0])).toBe(
      "/api/chats/order-actions/order_01HX/stage",
    );
    expect(patchMock.mock.calls[0][1]).toEqual({ stage: "ready" });
  });

  it("useTransitionOrderStage propaga success:false + error_detail", async () => {
    patchMock.mockResolvedValue({
      success: false,
      error_detail: "invalid_transition: ready→new",
    });
    const { result } = renderHook(() => useTransitionOrderStage("wa_1"), {
      wrapper: makeWrapper(),
    });
    const data = await result.current.mutateAsync({ orderId: "o1", stage: "new" });
    expect(data.success).toBe(false);
    expect(data.error_detail).toContain("invalid_transition");
  });

  it("orderRefKeys.bySession genera una key estable por sesión", () => {
    expect(orderRefKeys.bySession("wa_1")).toEqual([
      "chats",
      "order-ref",
      "by-session",
      "wa_1",
    ]);
  });
});
