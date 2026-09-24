/**
 * Borrador del editor de "Unidades con descuento" — UI state colocado, seed
 * desde las filas guardadas (el editor se remonta con `key` cuando cambian).
 * Reglas espejo de `validate_quota_rows`: color/aroma obligatorios si el
 * producto tiene esa lista y `null` si no; unidades enteras desde 1. El PUT
 * es todo o nada y el backend sigue siendo la última palabra.
 */

import type {
  CouponProduct,
  CouponUnitRow,
  CouponUnitsInput,
} from "@plugins/marketing/frontend/entities/coupon";

export interface UnitDraftRow {
  /** Clave local estable para React (las filas nuevas no tienen id). */
  key: string;
  /** Id de la fila guardada (para mostrar vendidas/quedan). */
  savedId: string | null;
  productId: string;
  /** Título guardado — por si el producto ya no está en el catálogo. */
  title: string;
  color: string | null;
  aroma: string | null;
  units: string;
}

export function draftRowsFromUnits(rows: CouponUnitRow[]): UnitDraftRow[] {
  return rows.map((r) => ({
    key: r.id,
    savedId: r.id,
    productId: r.productId,
    title: r.title,
    color: r.color,
    aroma: r.aroma,
    units: String(r.units),
  }));
}

export function newDraftRow(key: string): UnitDraftRow {
  return { key, savedId: null, productId: "", title: "", color: null, aroma: null, units: "1" };
}

/** Al cambiar de producto, color y aroma vuelven a vacío (la lista cambia). */
export function withProduct(row: UnitDraftRow, productId: string): UnitDraftRow {
  return { ...row, productId, title: "", color: null, aroma: null };
}

/** Errores por índice de fila (mismo orden que el PUT). */
export function validateUnitRows(
  rows: UnitDraftRow[],
  productsById: Map<string, CouponProduct>,
): Map<number, string[]> {
  const errors = new Map<number, string[]>();
  rows.forEach((row, i) => {
    const messages: string[] = [];
    const product = productsById.get(row.productId);
    if (!row.productId) messages.push("Elige el producto.");
    if (product && product.colors.length > 0 && !row.color) messages.push("Elige el color.");
    if (product && product.aromas.length > 0 && !row.aroma) messages.push("Elige el aroma.");
    if (!/^\d+$/.test(row.units.trim()) || Number(row.units) < 1)
      messages.push("Las unidades son un número entero desde 1.");
    if (messages.length > 0) errors.set(i, messages);
  });
  return errors;
}

export function draftToUnitsInput(
  rows: UnitDraftRow[],
  showUnitsLeft: boolean,
): CouponUnitsInput {
  return {
    rows: rows.map((r) => ({
      productId: r.productId,
      color: r.color,
      aroma: r.aroma,
      units: Number(r.units.trim()),
    })),
    showUnitsLeft,
  };
}
