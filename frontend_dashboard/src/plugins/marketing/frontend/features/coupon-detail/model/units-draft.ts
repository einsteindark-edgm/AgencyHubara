/**
 * Borrador del editor de "Unidades con descuento" — UI state colocado (un
 * reducer: regla #3), sembrado desde lo guardado. Reglas espejo de
 * `validate_quota_rows`: color/aroma obligatorios si el producto tiene esa
 * lista y `null` si no; unidades enteras desde 1; hasta 200 filas. El PUT es
 * todo o nada y el backend sigue siendo la última palabra.
 *
 * Premortem de la central de cupones:
 *  - D7 (C-5): el borrador recuerda la VERSIÓN que editó (`updatedAt`) y la
 *    manda al guardar; si otra persona guardó después, el PUT responde 409 y
 *    no se pisa lo suyo. Lo nuevo del servidor re-siembra el editor solo si
 *    el operador no cambió nada; con cambios, se avisa y él decide recargar.
 *  - D13: los errores del 422 vienen por índice del ENVÍO → se pegan a la
 *    fila que los tuvo (su `key`), no a la que hoy ocupa ese índice, y se
 *    borran al editarla; cambiar producto/color/aroma de una fila guardada
 *    la vuelve otra fila (sin las vendidas/quedan de la vieja).
 */

import type {
  CouponProduct,
  CouponUnitRow,
  CouponUnits,
  CouponUnitsInput,
} from "@plugins/marketing/frontend/entities/coupon";

/** Espejo del `max_length=200` del PUT (más filas = 422 crudo de pydantic). */
export const MAX_UNIT_ROWS = 200;

export interface UnitDraftRow {
  /** Clave local estable para React (las filas nuevas no tienen id). */
  key: string;
  /** Id de la fila guardada (para mostrar vendidas/quedan); null si es nueva
   *  o si se cambió su producto, color o aroma. */
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

/** Al cambiar de producto, color y aroma vuelven a vacío (la lista cambia) y
 *  la fila deja de ser la guardada. */
export function withProduct(row: UnitDraftRow, productId: string): UnitDraftRow {
  return { ...row, productId, title: "", color: null, aroma: null, savedId: null };
}

/** Otro color o aroma = otra combinación del cupo: ya no es la fila guardada. */
export function withAttribute(
  row: UnitDraftRow,
  patch: { color?: string | null; aroma?: string | null },
): UnitDraftRow {
  const changed =
    (patch.color !== undefined && patch.color !== row.color) ||
    (patch.aroma !== undefined && patch.aroma !== row.aroma);
  return { ...row, ...patch, savedId: changed ? null : row.savedId };
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
  expectedUpdatedAt: string | null,
): CouponUnitsInput {
  return {
    rows: rows.map((r) => ({
      productId: r.productId,
      color: r.color,
      aroma: r.aroma,
      units: Number(r.units.trim()),
    })),
    showUnitsLeft,
    expectedUpdatedAt,
  };
}

/** Firma de lo GUARDADO (versión + filas + preferencia). Las vendidas no
 *  cuentan: un pedido nuevo no es "otra persona cambió las unidades". */
export function unitsSignature(units: CouponUnits): string {
  return JSON.stringify([
    units.updatedAt,
    units.showUnitsLeft,
    units.rows.map((r) => [r.id, r.productId, r.color, r.aroma, r.units]),
  ]);
}

/* ── Reducer ─────────────────────────────────────────────────────────────── */

export interface UnitsDraftState {
  /** La versión guardada sobre la que se edita. */
  base: { signature: string; updatedAt: string | null };
  /** Una versión ya superada por un guardado propio: el refetch viejo que
   *  llega después no es "lo nuevo" (no re-siembra ni avisa). */
  supersededSignature: string | null;
  rows: UnitDraftRow[];
  showUnitsLeft: boolean;
  /** El operador cambió algo desde `base`: lo del servidor no lo pisa. */
  dirty: boolean;
  /** Se intentó guardar: se muestran los errores del formulario. */
  submitted: boolean;
  /** Claves de las filas en el orden del ÚLTIMO envío (índices del 422). */
  sentKeys: string[];
  /** Filas editadas o quitadas después de ese envío. */
  editedKeys: string[];
}

export type UnitsDraftAction =
  /** Lo guardado cambió y no hay cambios propios (o el operador recargó). */
  | { type: "reseed"; units: CouponUnits }
  /** El PUT propio salió bien: su respuesta es la versión nueva. */
  | { type: "saved"; units: CouponUnits }
  | { type: "patch"; row: UnitDraftRow }
  | { type: "add"; key: string }
  | { type: "remove"; key: string }
  | { type: "show-units-left"; value: boolean }
  | { type: "submit" }
  /** El PUT salió (pasó la validación local). */
  | { type: "sent" }
  /** "Recargar": el borrador cede ante lo que traiga el servidor. */
  | { type: "discard" };

export function initUnitsDraft(units: CouponUnits): UnitsDraftState {
  return {
    base: { signature: unitsSignature(units), updatedAt: units.updatedAt },
    supersededSignature: null,
    rows: draftRowsFromUnits(units.rows),
    showUnitsLeft: units.showUnitsLeft,
    dirty: false,
    submitted: false,
    sentKeys: [],
    editedKeys: [],
  };
}

const withKey = (keys: string[], key: string) => (keys.includes(key) ? keys : [...keys, key]);

export function unitsDraftReducer(
  state: UnitsDraftState,
  action: UnitsDraftAction,
): UnitsDraftState {
  switch (action.type) {
    case "reseed":
      return initUnitsDraft(action.units);
    case "saved":
      return { ...initUnitsDraft(action.units), supersededSignature: state.base.signature };
    case "patch":
      return {
        ...state,
        dirty: true,
        rows: state.rows.map((r) => (r.key === action.row.key ? action.row : r)),
        editedKeys: withKey(state.editedKeys, action.row.key),
      };
    case "add":
      if (state.rows.length >= MAX_UNIT_ROWS) return state;
      return { ...state, dirty: true, rows: [...state.rows, newDraftRow(action.key)] };
    case "remove":
      return {
        ...state,
        dirty: true,
        rows: state.rows.filter((r) => r.key !== action.key),
        editedKeys: withKey(state.editedKeys, action.key),
      };
    case "show-units-left":
      return { ...state, dirty: true, showUnitsLeft: action.value };
    case "submit":
      return { ...state, submitted: true };
    case "sent":
      return { ...state, sentKeys: state.rows.map((r) => r.key), editedKeys: [] };
    case "discard":
      return { ...state, dirty: false };
  }
}

/** ¿Lo guardado en el servidor es otra versión que la del borrador? */
export function serverChangedSince(state: UnitsDraftState, units: CouponUnits): boolean {
  const signature = unitsSignature(units);
  return signature !== state.base.signature && signature !== state.supersededSignature;
}

/** Errores del 422 (por índice del envío) → por fila, sin los de filas
 *  editadas después del envío. */
export function serverErrorsByKey(
  byIndex: Map<number, string[]>,
  state: Pick<UnitsDraftState, "sentKeys" | "editedKeys">,
): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const [index, messages] of byIndex) {
    const key = state.sentKeys[index];
    if (key !== undefined && !state.editedKeys.includes(key)) out.set(key, messages);
  }
  return out;
}
