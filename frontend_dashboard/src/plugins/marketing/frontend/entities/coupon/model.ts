/**
 * Modelo de dominio de la central de cupones (camelCase).
 *
 * Un cupón es una promoción de PORCENTAJE de Medusa + su campaña (D8). Lo que
 * Medusa tenga que la central no sabe editar llega con `manageable=false` y
 * el motivo: se ve en solo lectura (D7). El cupo por unidad vive en Hubara y
 * se puede poner a cualquier cupón de porcentaje (`acceptsUnits`).
 */

import { ApiError } from "@/shared/sdk";

import { backendFieldErrorSchema, backendRowsErrorSchema } from "./contracts";

export type CouponState = "draft" | "scheduled" | "active" | "paused" | "expired";

export interface CouponUnitsSummary {
  total: number;
  /** null = no se pudieron leer las ventas en Medusa. */
  left: number | null;
}

export interface Coupon {
  promotionId: string;
  campaignId: string | null;
  code: string;
  campaignName: string | null;
  /** null si no es una promoción de porcentaje (solo lectura). */
  percentage: number | null;
  /** Ids de producto, o "all" = todo el catálogo. */
  products: string[] | "all";
  /** Día calendario YYYY-MM-DD (Bogotá). */
  startsOn: string | null;
  /** Último día INCLUIDO, YYYY-MM-DD (Bogotá). */
  endsOn: string | null;
  /** "27 de septiembre" — lo que dice la plantilla de la campaña. */
  endsOnLabel: string | null;
  /** Estado en Medusa: draft | active | inactive. */
  status: string;
  /** Estado que ve el operador (derivado en el backend con la hora). */
  state: CouponState;
  manageable: boolean;
  unmanageableReason: string | null;
  acceptsUnits: boolean;
  units: CouponUnitsSummary | null;
}

export interface CouponUnitRow {
  id: string;
  productId: string;
  handle: string;
  title: string;
  color: string | null;
  aroma: string | null;
  units: number;
  sold: number;
  unitsLeft: number;
  /** Vendidas > unidades (el operador bajó el cupo). */
  oversold: boolean;
  createdBy: string;
}

export interface CouponUnits {
  rows: CouponUnitRow[];
  showUnitsLeft: boolean;
  /** Medusa no respondió: las vendidas no están al día. */
  unavailable: boolean;
}

export interface CouponChange {
  /** ISO UTC. */
  ts: string;
  actor: string;
  /** create | update | update_partial | set_status | delete | units */
  action: string;
  detail: Record<string, unknown>;
}

export interface CouponDetail {
  coupon: Coupon;
  units: CouponUnits;
  changes: CouponChange[];
}

export interface CouponSale {
  orderId: string;
  displayId: number | null;
  createdAt: string;
  isDraft: boolean;
  quotaUnits: number;
  discountCop: number;
}

export interface CouponSales {
  orders: number;
  discountCop: number;
  quotaUnits: number;
  sales: CouponSale[];
}

export interface CouponProduct {
  id: string;
  handle: string;
  title: string;
  /** Lista cerrada; vacía = el producto no tiene color (va null en el cupo). */
  colors: string[];
  aromas: string[];
}

/** Formulario de alta (POST). */
export interface CouponInput {
  code: string;
  campaignName: string;
  percentage: number;
  products: string[] | "all";
  startsOn: string;
  endsOn: string;
  status: "draft" | "active";
}

/** Edición parcial (PATCH): solo los campos que cambiaron. */
export type CouponPatch = Partial<Omit<CouponInput, "status">>;

/** Fila del cupo tal como se guarda (PUT, todo o nada). */
export interface CouponUnitRowInput {
  productId: string;
  color: string | null;
  aroma: string | null;
  units: number;
}

export interface CouponUnitsInput {
  rows: CouponUnitRowInput[];
  showUnitsLeft: boolean;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

export type CouponTone = "neutral" | "info" | "warn" | "ok" | "danger";

export const COUPON_STATE_META: Record<CouponState, { label: string; tone: CouponTone }> = {
  draft: { label: "Borrador", tone: "neutral" },
  scheduled: { label: "Programado", tone: "info" },
  active: { label: "Activo", tone: "ok" },
  paused: { label: "Pausado", tone: "warn" },
  expired: { label: "Vencido", tone: "danger" },
};

/** "quedan 3 de 5" — o solo el total si no se pudieron leer las ventas. */
export function couponUnitsLabel(u: CouponUnitsSummary): string {
  if (u.left === null) return u.total === 1 ? "1 unidad" : `${u.total} unidades`;
  return `quedan ${u.left} de ${u.total}`;
}

/** Una campaña solo puede anunciar un cupón que rige o va a regir. */
export function isCouponPickable(c: Coupon): boolean {
  return c.state === "active" || c.state === "scheduled";
}

/** Forma válida del código — espejo de `CENTRAL_CODE_RE` (3 a 14 letras o
 *  números en mayúscula; `VELAS_10` choca con el guard anti-leak del bot). */
export function sanitizeCouponCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 14);
}

function errorBody(err: unknown): unknown {
  return err instanceof ApiError ? err.body : null;
}

/** 422 del formulario → `{field, message}` (field en snake_case del backend). */
export function couponFieldError(err: unknown): { field: string; message: string } | null {
  const parsed = backendFieldErrorSchema.safeParse(errorBody(err));
  return parsed.success ? parsed.data.detail : null;
}

/** 422 del cupo → mensajes por índice de fila (0-based, orden del PUT). */
export function couponRowErrors(err: unknown): Map<number, string[]> {
  const byRow = new Map<number, string[]>();
  const parsed = backendRowsErrorSchema.safeParse(errorBody(err));
  if (!parsed.success) return byRow;
  for (const r of parsed.data.detail.rows) {
    byRow.set(r.row, [...(byRow.get(r.row) ?? []), r.message]);
  }
  return byRow;
}
