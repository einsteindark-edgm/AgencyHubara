/**
 * Hooks de la central de cupones contra una QueryClient REAL (el HTTP se
 * stubea): qué refresca un evento de pedidos (D12) y cómo quedan la lista y
 * la selección justo después de crear o borrar un cupón (D9).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

const handlers = vi.hoisted(() => ({} as Record<string, (e: unknown) => void>));
const http = vi.hoisted(() => ({ post: vi.fn(), delete: vi.fn() }));

vi.mock("@/shared/api", () => ({
  useDashboardEvents: (domain: string, handler: (e: unknown) => void) => {
    handlers[domain] = handler;
  },
  useInvalidateOnReconnect: () => {},
}));

vi.mock("@/shared/sdk", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  apiClient: {
    post: (...a: unknown[]) => http.post(...a),
    delete: (...a: unknown[]) => http.delete(...a),
  },
}));

import { useCouponOrdersEvents, useCreateCoupon, useDeleteCoupon } from "./api";
import { couponKeys } from "./keys";
import type { Coupon } from "./model";

function makeCoupon(promotionId: string, code: string): Coupon {
  return {
    promotionId,
    campaignId: null,
    code,
    campaignName: null,
    percentage: 10,
    products: "all",
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: null,
  };
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

beforeEach(() => {
  http.post.mockReset();
  http.delete.mockReset();
});

describe("useCouponOrdersEvents — un pedido nuevo o cancelado (D12)", () => {
  it("refresca la lista, el cupón abierto y sus ventas — no el catálogo ni otros cupones", () => {
    const { qc, wrapper } = setup();
    const keys = {
      list: couponKeys.list(),
      detail: couponKeys.detail("promo_01"),
      sales: couponKeys.sales("promo_01"),
      products: couponKeys.products(),
      otherDetail: couponKeys.detail("promo_02"),
    };
    for (const key of Object.values(keys)) qc.setQueryData(key, {});

    renderHook(() => useCouponOrdersEvents("promo_01"), { wrapper });
    handlers.orders!({ domain: "orders", type: "changed" });

    const invalidated = (key: readonly unknown[]) => qc.getQueryState(key)?.isInvalidated;
    expect(invalidated(keys.list)).toBe(true);
    expect(invalidated(keys.detail)).toBe(true);
    expect(invalidated(keys.sales)).toBe(true);
    expect(invalidated(keys.products)).toBe(false);
    expect(invalidated(keys.otherDetail)).toBe(false);
  });
});

describe("crear y borrar dejan la lista al día al instante (D9)", () => {
  it("el cupón creado entra a la lista con la respuesta del alta (se puede seleccionar ya)", async () => {
    const { qc, wrapper } = setup();
    qc.setQueryData(couponKeys.list(), [makeCoupon("promo_a", "AMOR27")]);
    http.post.mockResolvedValue({
      promotion_id: "promo_new",
      code: "NUEVO10",
      percentage: 10,
      status: "active",
      state: "active",
      manageable: true,
    });

    const { result } = renderHook(() => useCreateCoupon(), { wrapper });
    await act(() =>
      result.current.mutateAsync({
        code: "NUEVO10",
        campaignName: "",
        percentage: 10,
        products: "all",
        startsOn: "2026-09-24",
        endsOn: "2026-09-30",
        status: "active",
      }),
    );

    const ids = qc.getQueryData<Coupon[]>(couponKeys.list())?.map((c) => c.promotionId);
    expect(ids).toEqual(["promo_new", "promo_a"]);
  });

  it("el cupón borrado sale de la lista al instante (nada vuelve a pedir su detalle: sin 404)", async () => {
    const { qc, wrapper } = setup();
    qc.setQueryData(couponKeys.list(), [
      makeCoupon("promo_a", "AMOR27"),
      makeCoupon("promo_b", "PAPA20"),
    ]);
    http.delete.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeleteCoupon("promo_a"), { wrapper });
    await act(() => result.current.mutateAsync());

    const ids = qc.getQueryData<Coupon[]>(couponKeys.list())?.map((c) => c.promotionId);
    expect(ids).toEqual(["promo_b"]);
  });
});
