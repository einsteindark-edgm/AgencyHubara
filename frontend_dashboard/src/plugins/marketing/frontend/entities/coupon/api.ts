/**
 * Hooks de la central de cupones contra `/api/marketing/coupons*`.
 *
 * Todo boundary HTTP: `apiClient` del SDK + `{signal}` + Zod `.parse()` +
 * mapper snake→camel. Cada escritura invalida la lista y el detalle (el
 * estado, las unidades que quedan y el registro de cambios los recalcula el
 * backend). Los errores quedan en la mutation: los features los leen con
 * `apiErrorDetail` / `couponFieldError` / `couponRowErrors`.
 *
 * Realtime: el estado depende de la hora (programado → activo → vencido) y
 * las unidades que quedan salen de los pedidos de Medusa — la lista usa el
 * refetch numérico ≥60 s (red de seguridad, regla #2) y el evento `orders`
 * del stream invalida la lista, el cupón abierto y sus ventas
 * (`useCouponOrdersEvents`) — nunca el catálogo de productos (D12).
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useCallback } from "react";

import { useDashboardEvents, useInvalidateOnReconnect } from "@/shared/api";
import { apiClient } from "@/shared/sdk";

import {
  backendCouponDetailSchema,
  backendCouponProductsResponseSchema,
  backendCouponSalesSchema,
  backendCouponSchema,
  backendCouponsResponseSchema,
  backendCouponUnitsSchema,
  type BackendCoupon,
  type BackendCouponDetail,
  type BackendCouponProductsResponse,
  type BackendCouponSales,
  type BackendCouponUnits,
} from "./contracts";
import { couponKeys } from "./keys";
import type {
  Coupon,
  CouponDetail,
  CouponInput,
  CouponPatch,
  CouponProduct,
  CouponSales,
  CouponState,
  CouponUnits,
  CouponUnitsInput,
} from "./model";

const BASE = "/api/marketing/coupons";

/* ── Mappers backend → dominio ──────────────────────────────────────────── */

function asState(s: string): CouponState {
  if (
    s === "draft" ||
    s === "scheduled" ||
    s === "active" ||
    s === "paused" ||
    s === "expired"
  )
    return s;
  return "draft";
}

export function mapBackendCoupon(b: BackendCoupon): Coupon {
  return {
    promotionId: b.promotion_id,
    campaignId: b.campaign_id,
    code: b.code,
    campaignName: b.campaign_name,
    percentage: b.percentage,
    products: b.products === "all" ? "all" : [...b.products],
    startsOn: b.starts_on,
    endsOn: b.ends_on,
    endsOnLabel: b.ends_on_label,
    status: b.status,
    state: asState(b.state),
    manageable: b.manageable,
    unmanageableReason: b.unmanageable_reason,
    acceptsUnits: b.accepts_units,
    units: b.units === null ? null : { total: b.units.total, left: b.units.left },
  };
}

export function mapBackendCouponUnits(b: BackendCouponUnits): CouponUnits {
  return {
    rows: b.rows.map((r) => ({
      id: r.id,
      productId: r.product_id,
      handle: r.handle,
      title: r.title,
      color: r.color,
      aroma: r.aroma,
      units: r.units,
      sold: r.sold,
      unitsLeft: r.units_left,
      oversold: r.oversold,
      createdBy: r.created_by,
    })),
    showUnitsLeft: b.show_units_left,
    unavailable: b.unavailable,
    updatedAt: b.updated_at,
  };
}

export function mapBackendCouponDetail(b: BackendCouponDetail): CouponDetail {
  return {
    coupon: mapBackendCoupon(b.coupon),
    units: mapBackendCouponUnits(b.units),
    changes: b.changes.map((c) => ({
      ts: c.ts,
      actor: c.actor,
      action: c.action,
      detail: c.detail,
    })),
  };
}

export function mapBackendCouponSales(b: BackendCouponSales): CouponSales {
  return {
    orders: b.orders,
    discountCop: b.discount_cop,
    quotaUnits: b.quota_units,
    sales: b.sales.map((s) => ({
      orderId: s.order_id,
      displayId: s.display_id,
      createdAt: s.created_at,
      isDraft: s.is_draft,
      quotaUnits: s.quota_units,
      discountCop: s.discount_cop,
    })),
  };
}

export function mapBackendCouponProducts(
  b: BackendCouponProductsResponse,
): CouponProduct[] {
  return b.products.map((p) => ({
    id: p.id,
    handle: p.handle,
    title: p.title,
    colors: [...p.colors],
    aromas: [...p.aromas],
  }));
}

/* ── Dominio → body ─────────────────────────────────────────────────────── */

export function couponInputToBody(input: CouponInput): Record<string, unknown> {
  return {
    code: input.code,
    campaign_name: input.campaignName,
    percentage: input.percentage,
    products: input.products,
    starts_on: input.startsOn,
    ends_on: input.endsOn,
    status: input.status,
  };
}

/** PATCH parcial: solo los campos presentes. */
export function couponPatchToBody(patch: CouponPatch): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (patch.code !== undefined) body.code = patch.code;
  if (patch.campaignName !== undefined) body.campaign_name = patch.campaignName;
  if (patch.percentage !== undefined) body.percentage = patch.percentage;
  if (patch.products !== undefined) body.products = patch.products;
  if (patch.startsOn !== undefined) body.starts_on = patch.startsOn;
  if (patch.endsOn !== undefined) body.ends_on = patch.endsOn;
  return body;
}

export function couponUnitsToBody(input: CouponUnitsInput): Record<string, unknown> {
  return {
    rows: input.rows.map((r) => ({
      product_id: r.productId,
      color: r.color,
      aroma: r.aroma,
      units: r.units,
    })),
    show_units_left: input.showUnitsLeft,
    // C-5: la versión editada (null = nunca guardado) viaja tal cual.
    ...(input.expectedUpdatedAt !== undefined
      ? { expected_updated_at: input.expectedUpdatedAt }
      : {}),
  };
}

const idPath = (promotionId: string) => `${BASE}/${encodeURIComponent(promotionId)}`;

/* ── Queries ────────────────────────────────────────────────────────────── */

/** Todos los cupones de Medusa (gestionables o no) con el cupo resumido.
 *  `enabled` permite el fetch lazy desde el constructor de campañas. */
export function useCoupons(enabled = true) {
  return useQuery<Coupon[]>({
    queryKey: couponKeys.list(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(BASE, { signal });
      return backendCouponsResponseSchema.parse(raw).coupons.map(mapBackendCoupon);
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
    enabled,
  });
}

/** Detalle: cupón + filas del cupo + registro de cambios. */
export function useCoupon(promotionId: string | null) {
  return useQuery<CouponDetail>({
    queryKey: couponKeys.detail(promotionId ?? ""),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(idPath(promotionId ?? ""), { signal });
      return mapBackendCouponDetail(backendCouponDetailSchema.parse(raw));
    },
    staleTime: 30_000,
    enabled: Boolean(promotionId),
  });
}

/** Ventas del cupón: pedidos, descuento total y unidades del cupo. */
export function useCouponSales(promotionId: string | null) {
  return useQuery<CouponSales>({
    queryKey: couponKeys.sales(promotionId ?? ""),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(`${idPath(promotionId ?? "")}/sales`, {
        signal,
      });
      return mapBackendCouponSales(backendCouponSalesSchema.parse(raw));
    },
    staleTime: 30_000,
    enabled: Boolean(promotionId),
  });
}

/** Productos del catálogo con sus listas de color y aroma. */
export function useCouponProducts(enabled = true) {
  return useQuery<CouponProduct[]>({
    queryKey: couponKeys.products(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/marketing/coupon-products", {
        signal,
      });
      return mapBackendCouponProducts(backendCouponProductsResponseSchema.parse(raw));
    },
    staleTime: 5 * 60_000,
    enabled,
  });
}

/** Un pedido nuevo o cancelado mueve las unidades que quedan y las ventas:
 *  refresca la lista y el cupón abierto (`promotionId`) con sus ventas. El
 *  catálogo de productos NO depende de los pedidos (D12). */
export function useCouponOrdersEvents(promotionId: string | null): void {
  const qc = useQueryClient();
  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: couponKeys.list() });
    if (promotionId) {
      qc.invalidateQueries({ queryKey: couponKeys.detail(promotionId) });
      qc.invalidateQueries({ queryKey: couponKeys.sales(promotionId) });
    }
  }, [qc, promotionId]);
  useDashboardEvents("orders", invalidate);
  useInvalidateOnReconnect(invalidate);
}

/* ── Mutations ──────────────────────────────────────────────────────────── */

function useInvalidateCoupon(promotionId?: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: couponKeys.list() });
    if (promotionId) {
      qc.invalidateQueries({ queryKey: couponKeys.detail(promotionId) });
      qc.invalidateQueries({ queryKey: couponKeys.sales(promotionId) });
    }
  };
}

/** POST /coupons — 409 "Ese código ya existe.", 422 `{field, message}`.
 *  El cupón creado entra a la lista con la respuesta del alta (D9): el Page
 *  lo selecciona YA, sin esperar el refetch de la lista (que igual corre). */
export function useCreateCoupon() {
  const qc = useQueryClient();
  const invalidate = useInvalidateCoupon();
  return useMutation<Coupon, Error, CouponInput>({
    mutationFn: async (input) => {
      const raw = await apiClient.post<unknown>(BASE, couponInputToBody(input));
      return mapBackendCoupon(backendCouponSchema.parse(raw));
    },
    onSuccess: (created) => {
      qc.setQueryData<Coupon[]>(couponKeys.list(), (list) =>
        list
          ? [created, ...list.filter((c) => c.promotionId !== created.promotionId)]
          : list,
      );
      invalidate();
    },
  });
}

/** La edición del cupón. El detalle es su dueño (D6): el formulario se
 *  re-siembra con lo que quedó en Medusa y el error — p. ej. el 502 "quedó a
 *  medias" — tiene que sobrevivir a ese re-montaje. */
export type CouponUpdateMutation = UseMutationResult<Coupon, Error, CouponPatch>;

/** PATCH /coupons/{id} — 409 si no es gestionable; 502 si quedó a medias
 *  (por eso se invalida también en error: el detalle trae lo real). */
export function useUpdateCoupon(promotionId: string): CouponUpdateMutation {
  const invalidate = useInvalidateCoupon(promotionId);
  return useMutation<Coupon, Error, CouponPatch>({
    mutationFn: async (patch) => {
      const raw = await apiClient.patch<unknown>(
        idPath(promotionId),
        couponPatchToBody(patch),
      );
      return mapBackendCoupon(backendCouponSchema.parse(raw));
    },
    onSettled: invalidate,
  });
}

/** POST /coupons/{id}/status — pausar (`inactive`) o activar (`active`). */
export function useSetCouponStatus(promotionId: string) {
  const invalidate = useInvalidateCoupon(promotionId);
  return useMutation<Coupon, Error, "active" | "inactive">({
    mutationFn: async (status) => {
      const raw = await apiClient.post<unknown>(`${idPath(promotionId)}/status`, {
        status,
      });
      return mapBackendCoupon(backendCouponSchema.parse(raw));
    },
    onSuccess: invalidate,
  });
}

/** DELETE /coupons/{id} — solo borradores sin ventas (409 si no). El cupón
 *  sale de la lista AL INSTANTE (D9): si no, la selección caía de nuevo en
 *  él hasta el refetch y su detalle (ya borrado) se pedía otra vez → 404. */
export function useDeleteCoupon(promotionId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await apiClient.delete<unknown>(idPath(promotionId));
    },
    onSuccess: () => {
      qc.setQueryData<Coupon[]>(couponKeys.list(), (list) =>
        list?.filter((c) => c.promotionId !== promotionId),
      );
      qc.removeQueries({ queryKey: couponKeys.detail(promotionId) });
      qc.removeQueries({ queryKey: couponKeys.sales(promotionId) });
      qc.invalidateQueries({ queryKey: couponKeys.list() });
    },
  });
}

/** PUT /coupons/{id}/units — reemplaza las filas del cupo (todo o nada;
 *  422 con errores por fila; 409 `units_changed` si otra persona guardó
 *  después de la versión editada). Devuelve lo guardado con su versión. */
export function usePutCouponUnits(promotionId: string) {
  const invalidate = useInvalidateCoupon(promotionId);
  return useMutation<CouponUnits, Error, CouponUnitsInput>({
    mutationFn: async (input) => {
      const raw = await apiClient.put<unknown>(
        `${idPath(promotionId)}/units`,
        couponUnitsToBody(input),
      );
      return mapBackendCouponUnits(backendCouponUnitsSchema.parse(raw));
    },
    onSuccess: invalidate,
  });
}
