/**
 * Texto del registro de cambios del cupón (quién / qué / cuándo). El
 * `detail` de cada entrada lo arma el backend (`audit_diff`: `{campo: [antes,
 * después]}`; `set_status`: `{status}`; `units`: `{rows, show_units_left}`).
 */

import type { CouponChange } from "@plugins/marketing/frontend/entities/coupon";

const ACTION_LABEL: Record<string, string> = {
  create: "Creó el cupón",
  update: "Editó el cupón",
  update_partial: "Edición a medias en Medusa",
  set_status: "Cambió el estado",
  delete: "Borró el cupón",
  units: "Cambió las unidades con descuento",
};

const FIELD_LABEL: Record<string, string> = {
  code: "código",
  campaign_name: "campaña",
  percentage: "descuento",
  products: "productos",
  starts_on: "desde",
  ends_on: "hasta",
  status: "estado",
  failed_step: "paso que falló",
  show_units_left: "mostrar cuántas quedan",
};

const STATUS_LABEL: Record<string, string> = {
  active: "activo",
  inactive: "pausado",
  draft: "borrador",
};

export function changeActionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (value === "all") return "todo el catálogo";
  if (typeof value === "boolean") return value ? "sí" : "no";
  if (Array.isArray(value)) return value.map(show).join(", ");
  if (typeof value === "string") return STATUS_LABEL[value] ?? value;
  return String(value);
}

/** Líneas legibles del detalle ("descuento: 10 → 15"). */
export function changeSummary(change: CouponChange): string[] {
  const lines: string[] = [];
  for (const [key, value] of Object.entries(change.detail)) {
    const label = FIELD_LABEL[key] ?? key;
    if (key === "rows" && Array.isArray(value)) {
      lines.push(...value.map((r) => String(r)));
    } else if (Array.isArray(value) && value.length === 2 && change.action.startsWith("update")) {
      lines.push(`${label}: ${show(value[0])} → ${show(value[1])}`);
    } else {
      lines.push(`${label}: ${show(value)}`);
    }
  }
  return lines;
}
