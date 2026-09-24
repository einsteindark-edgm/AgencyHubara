/**
 * "Unidades con descuento" — el cupo por unidad del cupón (producto + color
 * + aroma + cantidad). Cada fila elige el producto entre los del cupón y
 * luego color/aroma de las listas cerradas de ESE producto (un selector sin
 * lista no aparece y el valor va null). Guardar reemplaza todas las filas
 * (todo o nada); el 422 marca cada fila con su motivo.
 *
 * El borrador sigue a lo guardado mientras el operador no cambie nada; con
 * cambios propios, lo de otra persona NO lo pisa: se avisa y "Recargar" lo
 * trae (D7). Guardar manda la versión editada: si alguien guardó después, el
 * 409 lo dice sin perder el borrador (C-5).
 */

import { useMemo, useReducer, useRef } from "react";

import { Icon } from "@/shared/ui";

import {
  couponRowErrors,
  isCouponUnitsConflict,
  usePutCouponUnits,
  type Coupon,
  type CouponProduct,
  type CouponUnits,
} from "@plugins/marketing/frontend/entities/coupon";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";

import {
  MAX_UNIT_ROWS,
  draftToUnitsInput,
  initUnitsDraft,
  serverChangedSince,
  serverErrorsByKey,
  unitsDraftReducer,
  validateUnitRows,
  withAttribute,
  withProduct,
} from "../model/units-draft";

interface Props {
  coupon: Coupon;
  /** Lo guardado (del detalle del cupón). */
  units: CouponUnits;
  products: CouponProduct[];
  /** Vuelve a pedir el detalle ("Recargar" tras un cambio de otra persona). */
  onReload: () => void;
}

const SELECT_CLS =
  "w-full rounded-md border border-line bg-canvas px-2 py-1 text-[12px] text-fg outline-none focus:border-accent";

export function UnitsEditor({ coupon, units, products, onReload }: Props) {
  const [draft, dispatch] = useReducer(unitsDraftReducer, units, initUnitsDraft);
  const nextKey = useRef(0);
  const save = usePutCouponUnits(coupon.promotionId);
  const { rows, showUnitsLeft, submitted } = draft;

  // Lo guardado cambió (otra persona, o el refetch de un guardado): sin
  // cambios propios el editor lo sigue (ajuste en render, sin efecto); con
  // cambios, NO se pisan — se avisa y el operador recarga.
  const serverChanged = serverChangedSince(draft, units);
  if (serverChanged && !draft.dirty) {
    dispatch({ type: "reseed", units });
  }

  const productsById = useMemo(() => new Map(products.map((p) => [p.id, p])), [products]);
  // Solo los productos del cupón (o todo el catálogo), en el orden del catálogo.
  const allowed = useMemo(
    () =>
      coupon.products === "all"
        ? products
        : products.filter((p) => (coupon.products as string[]).includes(p.id)),
    [coupon.products, products],
  );
  const savedById = useMemo(() => new Map(units.rows.map((r) => [r.id, r])), [units.rows]);

  const clientErrors = validateUnitRows(rows, productsById);
  // D13: el 422 viene por índice del ENVÍO → a la fila que lo tuvo.
  const serverErrors = serverErrorsByKey(couponRowErrors(save.error), draft);
  const rowErrors = (i: number, key: string): string[] =>
    submitted && clientErrors.has(i) ? clientErrors.get(i)! : (serverErrors.get(key) ?? []);
  const generalError = save.error ? apiErrorDetail(save.error) : null;
  const conflict = isCouponUnitsConflict(save.error);
  const atCap = rows.length >= MAX_UNIT_ROWS;

  const addRow = () => {
    nextKey.current += 1;
    dispatch({ type: "add", key: `new-${nextKey.current}` });
  };

  const reload = () => {
    dispatch({ type: "discard" });
    save.reset();
    onReload();
  };

  const onSave = () => {
    dispatch({ type: "submit" });
    if (clientErrors.size > 0) return;
    dispatch({ type: "sent" });
    save.mutate(draftToUnitsInput(rows, showUnitsLeft, draft.base.updatedAt), {
      // La respuesta del PUT es la versión nueva: la próxima vez se edita
      // sobre ella (y el refetch viejo que llegue no cuenta como cambio).
      onSuccess: (saved) => dispatch({ type: "saved", units: saved }),
    });
  };

  return (
    <div className="flex flex-col gap-2.5">
      {units.unavailable ? (
        <p className="text-[11px] text-warn">
          No se pudieron leer las ventas en Medusa: las vendidas no están al día.
        </p>
      ) : null}

      <table className="w-full border-collapse text-left text-[12px]">
        <thead>
          <tr className="text-[10.5px] font-medium uppercase tracking-wide text-fg-faint">
            <th className="py-1 pr-2 font-medium">Producto</th>
            <th className="py-1 pr-2 font-medium">Color</th>
            <th className="py-1 pr-2 font-medium">Aroma</th>
            <th className="w-20 py-1 pr-2 font-medium">Unidades</th>
            <th className="w-16 py-1 pr-2 text-right font-medium">Vendidas</th>
            <th className="w-16 py-1 pr-2 text-right font-medium">Quedan</th>
            <th className="w-8 py-1" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const n = i + 1;
            const product = productsById.get(row.productId);
            const saved = row.savedId ? savedById.get(row.savedId) : undefined;
            const options =
              row.productId && !allowed.some((p) => p.id === row.productId)
                ? [...allowed, { id: row.productId, title: row.title || row.productId }]
                : allowed;
            const errors = rowErrors(i, row.key);
            return (
              <tr key={row.key} aria-label={`fila ${n}`} className="border-t border-line align-top">
                <td className="py-1.5 pr-2">
                  <select
                    aria-label={`Producto de la fila ${n}`}
                    value={row.productId}
                    onChange={(e) =>
                      dispatch({ type: "patch", row: withProduct(row, e.target.value) })
                    }
                    className={SELECT_CLS}
                  >
                    <option value="">Elige el producto</option>
                    {options.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title}
                      </option>
                    ))}
                  </select>
                  {errors.length > 0 ? (
                    <ul className="mt-1 flex flex-col gap-0.5">
                      {errors.map((m) => (
                        <li key={m} className="text-[10.5px] leading-snug text-danger">
                          {m}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {saved?.oversold ? (
                    <p className="mt-1 text-[10.5px] leading-snug text-warn">
                      Se vendieron más de las que hay en el cupo ({saved.sold} vendidas).
                    </p>
                  ) : null}
                </td>
                <td className="py-1.5 pr-2">
                  <AttributeSelect
                    label={`Color de la fila ${n}`}
                    placeholder="Elige el color"
                    values={product?.colors}
                    value={row.color}
                    onChange={(color) =>
                      dispatch({ type: "patch", row: withAttribute(row, { color }) })
                    }
                  />
                </td>
                <td className="py-1.5 pr-2">
                  <AttributeSelect
                    label={`Aroma de la fila ${n}`}
                    placeholder="Elige el aroma"
                    values={product?.aromas}
                    value={row.aroma}
                    onChange={(aroma) =>
                      dispatch({ type: "patch", row: withAttribute(row, { aroma }) })
                    }
                  />
                </td>
                <td className="py-1.5 pr-2">
                  <input
                    aria-label={`Unidades de la fila ${n}`}
                    type="text"
                    inputMode="numeric"
                    maxLength={5}
                    value={row.units}
                    onChange={(e) =>
                      dispatch({ type: "patch", row: { ...row, units: e.target.value } })
                    }
                    className={SELECT_CLS + " tabular-nums"}
                  />
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-fg-muted">
                  {saved ? saved.sold : "—"}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-fg">
                  {saved ? saved.unitsLeft : "—"}
                </td>
                <td className="py-1.5 text-right">
                  <button
                    type="button"
                    aria-label={`Quitar la fila ${n}`}
                    onClick={() => dispatch({ type: "remove", key: row.key })}
                    className="rounded p-1 text-fg-faint hover:bg-white/[0.05] hover:text-danger"
                  >
                    <Icon.trash />
                  </button>
                </td>
              </tr>
            );
          })}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={7} className="py-3 text-center text-[11.5px] text-fg-faint">
                Sin cupo: el descuento aplica a los productos del cupón sin límite de unidades.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={addRow}
          disabled={atCap}
          className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[11.5px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
        >
          <Icon.plus />
          Agregar fila
        </button>
        {atCap ? (
          <span className="text-[11px] text-fg-muted">Un cupón lleva hasta {MAX_UNIT_ROWS} filas.</span>
        ) : null}
        <label className="flex items-center gap-1.5 text-[12px] text-fg-soft">
          <input
            type="checkbox"
            checked={showUnitsLeft}
            onChange={(e) => dispatch({ type: "show-units-left", value: e.target.checked })}
            className="accent-accent"
          />
          Mostrar al cliente cuántas quedan
        </label>
        <span className="ml-auto flex items-center gap-2">
          {save.isPending ? <span className="text-[11px] text-fg-muted">Guardando…</span> : null}
          <button
            type="button"
            disabled={save.isPending}
            onClick={onSave}
            className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Guardar unidades
          </button>
        </span>
      </div>

      {generalError ? (
        <div
          role="alert"
          className="flex items-center gap-3 rounded-md bg-danger-soft px-3 py-2 text-[11.5px] text-danger"
        >
          <span>{generalError}</span>
          {conflict ? <ReloadButton onClick={reload} /> : null}
        </div>
      ) : serverChanged && draft.dirty ? (
        <div className="flex items-center gap-3 rounded-md bg-warn-soft px-3 py-2 text-[11.5px] text-warn">
          <span>
            Otra persona cambió las unidades de este cupón mientras editabas. Recarga para ver lo
            nuevo (lo que no guardaste se pierde).
          </span>
          <ReloadButton onClick={reload} />
        </div>
      ) : null}
    </div>
  );
}

function ReloadButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="ml-auto shrink-0 rounded-md border border-current px-2.5 py-1 text-[11.5px] font-semibold hover:opacity-80"
    >
      Recargar
    </button>
  );
}

/** Selector de color/aroma: sin lista (o producto desconocido) no hay selector. */
function AttributeSelect({
  label,
  placeholder,
  values,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  values: string[] | undefined;
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  if (!values || values.length === 0) {
    return <span className="text-[11.5px] text-fg-faint">{value ?? "—"}</span>;
  }
  return (
    <select
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className={SELECT_CLS}
    >
      <option value="">{placeholder}</option>
      {values.map((v) => (
        <option key={v} value={v}>
          {v}
        </option>
      ))}
    </select>
  );
}
