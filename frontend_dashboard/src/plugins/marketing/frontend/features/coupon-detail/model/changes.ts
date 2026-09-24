/**
 * Texto del registro de cambios del cupón (quién / qué / cuándo). El
 * `detail` de cada entrada lo arma el backend (`audit_diff`: `{campo: [antes,
 * después]}`; `set_status`: `{status}`; `units`: `{rows, show_units_left}`;
 * `update_partial` suma `failed_step`). Todo en español para el operador:
 * los pasos de Medusa con su nombre y los productos por su título (D14).
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

/** Pasos de una edición en Medusa (`CouponPartialUpdateError.step`). */
const STEP_LABEL: Record<string, string> = {
  promotion: "código y descuento",
  products: "productos",
  campaign: "campaña y fechas",
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

/** Productos del cupón: "todo el catálogo" o los títulos (el id si el
 *  producto ya no está en el catálogo). */
function showProducts(value: unknown, titles: ReadonlyMap<string, string>): string {
  if (value === "all") return "todo el catálogo";
  if (!Array.isArray(value)) return show(value);
  if (value.length === 0) return "—";
  return value.map((id) => titles.get(String(id)) ?? String(id)).join(", ");
}

/** Líneas legibles del detalle ("descuento: 10 → 15"). `productTitles`:
 *  id → título del catálogo, para no mostrar ids crudos. */
export function changeSummary(
  change: CouponChange,
  productTitles: ReadonlyMap<string, string> = new Map(),
): string[] {
  const lines: string[] = [];
  for (const [key, value] of Object.entries(change.detail)) {
    const label = FIELD_LABEL[key] ?? key;
    const fmt =
      key === "products"
        ? (v: unknown) => showProducts(v, productTitles)
        : key === "failed_step"
          ? (v: unknown) => STEP_LABEL[String(v)] ?? show(v)
          : show;
    if (key === "rows" && Array.isArray(value)) {
      lines.push(...value.map((r) => String(r)));
    } else if (Array.isArray(value) && value.length === 2 && change.action.startsWith("update")) {
      lines.push(`${label}: ${fmt(value[0])} → ${fmt(value[1])}`);
    } else {
      lines.push(`${label}: ${fmt(value)}`);
    }
  }
  return lines;
}
