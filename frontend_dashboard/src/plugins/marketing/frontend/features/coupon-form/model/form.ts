/**
 * Estado del formulario del cupón — UI state colocado (regla #3), seed desde
 * el cupón al montar (`key` en el Page/detalle lo resetea). Las reglas son
 * espejo de `parse_coupon_spec` del backend para avisar ANTES de enviar; el
 * backend sigue siendo la última palabra (sus 422 se pegan al campo).
 */

import type {
  Coupon,
  CouponInput,
  CouponPatch,
} from "@plugins/marketing/frontend/entities/coupon";

export type CouponFormField =
  | "code"
  | "campaignName"
  | "percentage"
  | "products"
  | "startsOn"
  | "endsOn";

export interface CouponFormValues {
  code: string;
  campaignName: string;
  /** Texto tal cual lo escribe el operador (se valida como entero). */
  percentage: string;
  productsMode: "all" | "selected";
  products: string[];
  startsOn: string;
  endsOn: string;
}

export type CouponFormErrors = Partial<Record<CouponFormField, string>>;

const CODE_RE = /^[A-Z0-9]{3,14}$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
export const CAMPAIGN_NAME_MAX = 80;

export function emptyCouponForm(startsOn: string, endsOn: string): CouponFormValues {
  return {
    code: "",
    campaignName: "",
    percentage: "",
    productsMode: "all",
    products: [],
    startsOn,
    endsOn,
  };
}

export function formFromCoupon(c: Coupon): CouponFormValues {
  return {
    code: c.code,
    campaignName: c.campaignName ?? "",
    percentage: c.percentage === null ? "" : String(c.percentage),
    productsMode: c.products === "all" ? "all" : "selected",
    products: c.products === "all" ? [] : [...c.products],
    startsOn: c.startsOn ?? "",
    endsOn: c.endsOn ?? "",
  };
}

export function validateCouponForm(v: CouponFormValues): CouponFormErrors {
  const errors: CouponFormErrors = {};
  if (!CODE_RE.test(v.code)) {
    errors.code = "El código lleva de 3 a 14 letras o números.";
  }
  if (v.campaignName.trim().length > CAMPAIGN_NAME_MAX) {
    errors.campaignName = `El nombre de la campaña va hasta ${CAMPAIGN_NAME_MAX} caracteres.`;
  }
  const pct = v.percentage.trim();
  const n = Number(pct);
  if (!/^\d+$/.test(pct) || n < 1 || n > 100) {
    errors.percentage = "El descuento es un número entero entre 1 y 100.";
  }
  if (v.productsMode === "selected" && v.products.length === 0) {
    errors.products = "Elige al menos un producto o todo el catálogo.";
  }
  if (!DAY_RE.test(v.startsOn)) {
    errors.startsOn = "Elige el día en que empieza.";
  }
  if (!DAY_RE.test(v.endsOn)) {
    errors.endsOn = "Elige el último día del cupón.";
  } else if (DAY_RE.test(v.startsOn) && v.endsOn < v.startsOn) {
    errors.endsOn = 'El "hasta" no puede ser antes del "desde".';
  }
  return errors;
}

function productsOf(v: CouponFormValues): string[] | "all" {
  return v.productsMode === "all" ? "all" : [...v.products];
}

export function formToInput(
  v: CouponFormValues,
  status: CouponInput["status"],
): CouponInput {
  return {
    code: v.code,
    campaignName: v.campaignName.trim(),
    percentage: Number(v.percentage.trim()),
    products: productsOf(v),
    startsOn: v.startsOn,
    endsOn: v.endsOn,
    status,
  };
}

function sameProducts(a: string[] | "all", b: string[] | "all"): boolean {
  if (a === "all" || b === "all") return a === b;
  return a.length === b.length && [...a].sort().join("\n") === [...b].sort().join("\n");
}

/** PATCH con SOLO los campos que cambiaron respecto al cupón. */
export function formToPatch(v: CouponFormValues, c: Coupon): CouponPatch {
  const before = formFromCoupon(c);
  const patch: CouponPatch = {};
  if (v.code !== before.code) patch.code = v.code;
  if (v.campaignName.trim() !== before.campaignName.trim())
    patch.campaignName = v.campaignName.trim();
  if (v.percentage.trim() !== before.percentage) patch.percentage = Number(v.percentage.trim());
  if (!sameProducts(productsOf(v), productsOf(before))) patch.products = productsOf(v);
  if (v.startsOn !== before.startsOn) patch.startsOn = v.startsOn;
  if (v.endsOn !== before.endsOn) patch.endsOn = v.endsOn;
  return patch;
}

/** Campo del backend (snake_case) → campo del formulario. */
export function formFieldFromBackend(field: string): CouponFormField | null {
  switch (field) {
    case "code":
      return "code";
    case "campaign_name":
      return "campaignName";
    case "percentage":
      return "percentage";
    case "products":
      return "products";
    case "starts_on":
      return "startsOn";
    case "ends_on":
      return "endsOn";
    default:
      return null;
  }
}
