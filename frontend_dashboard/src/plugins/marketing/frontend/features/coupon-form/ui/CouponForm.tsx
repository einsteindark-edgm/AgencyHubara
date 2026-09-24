/**
 * Formulario del cupón — alta (sin `coupon`) y edición (con `coupon`).
 *
 * Alta: "Guardar borrador" / "Crear y activar" (POST). Edición: "Guardar
 * cambios" (PATCH solo con lo que cambió). El código se edita solo en alta o
 * mientras el cupón es borrador. Un cupón que la central no sabe editar
 * (`manageable=false`, D7) se muestra entero deshabilitado — el motivo lo
 * pinta el detalle.
 *
 * Validación inline (espejo del backend) al tocar cada campo y al enviar;
 * el 422 del backend se pega a su campo, los demás errores (409 código
 * ocupado, 503, 502 edición a medias) van en un aviso con su mensaje.
 *
 * En edición, la mutación la pone el detalle (`update`, D6): el detalle
 * re-siembra este formulario tras una edición a medias y el aviso tiene que
 * seguir a la vista.
 */

import { useId, useState, type ReactNode } from "react";

import { addDaysBogotaIso, todayBogotaIso } from "@/shared/lib";

import {
  couponFieldError,
  sanitizeCouponCode,
  useCouponProducts,
  useCreateCoupon,
  useUpdateCoupon,
  type Coupon,
  type CouponInput,
  type CouponUpdateMutation,
} from "@plugins/marketing/frontend/entities/coupon";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";

import {
  CAMPAIGN_NAME_MAX,
  emptyCouponForm,
  formFieldFromBackend,
  formFromCoupon,
  formToInput,
  formToPatch,
  validateCouponForm,
  type CouponFormField,
  type CouponFormValues,
} from "../model/form";

interface Props {
  /** Sin cupón = alta. */
  coupon?: Coupon;
  /** Edición: la mutación del detalle (sobrevive al re-sembrado). Sin ella,
   *  el formulario usa una propia. */
  update?: CouponUpdateMutation;
  /** Alta: el Page selecciona el cupón creado. */
  onCreated?: (promotionId: string) => void;
  onCancel?: () => void;
}

const INPUT_CLS =
  "w-full rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60 read-only:opacity-70 placeholder:text-fg-faint";

export function CouponForm({ coupon, update: liftedUpdate, onCreated, onCancel }: Props) {
  const isNew = coupon === undefined;
  const readOnly = !isNew && !coupon.manageable;
  const codeEditable = isNew || coupon.status === "draft";

  const [values, setValues] = useState<CouponFormValues>(() =>
    coupon ? formFromCoupon(coupon) : emptyCouponForm(todayBogotaIso(), addDaysBogotaIso(7)),
  );
  const [touched, setTouched] = useState<Partial<Record<CouponFormField, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);

  const create = useCreateCoupon();
  const ownUpdate = useUpdateCoupon(coupon?.promotionId ?? "");
  const update = liftedUpdate ?? ownUpdate;
  const mutation = isNew ? create : update;
  const {
    data: products = [],
    isPending: productsLoading,
    error: productsError,
  } = useCouponProducts();

  const clientErrors = validateCouponForm(values);
  const backend = couponFieldError(mutation.error);
  const backendField = backend ? formFieldFromBackend(backend.field) : null;
  const errorFor = (f: CouponFormField): string | undefined => {
    if ((submitted || touched[f]) && clientErrors[f]) return clientErrors[f];
    if (backendField === f) return backend?.message;
    return undefined;
  };
  const generalError =
    mutation.error && backendField === null ? apiErrorDetail(mutation.error) : null;

  const set = (p: Partial<CouponFormValues>) => setValues((v) => ({ ...v, ...p }));
  const touch = (f: CouponFormField) => setTouched((t) => ({ ...t, [f]: true }));

  const submit = (status: CouponInput["status"]) => {
    setSubmitted(true);
    if (Object.keys(clientErrors).length > 0) return;
    if (isNew) {
      create.mutate(formToInput(values, status), {
        onSuccess: (c) => onCreated?.(c.promotionId),
      });
    } else {
      const patch = formToPatch(values, coupon);
      if (Object.keys(patch).length > 0) update.mutate(patch);
    }
  };

  const toggleProduct = (id: string) => {
    touch("products");
    set({
      products: values.products.includes(id)
        ? values.products.filter((p) => p !== id)
        : [...values.products, id],
    });
  };

  return (
    <form
      noValidate
      onSubmit={(e) => e.preventDefault()}
      className="flex flex-col gap-3"
      aria-label={isNew ? "Nuevo cupón" : `Cupón ${coupon.code}`}
    >
      <div className="grid grid-cols-2 gap-2.5 max-[900px]:grid-cols-1">
        <Field label="Código" error={errorFor("code")}>
          {(id, describedBy) => (
            <input
              id={id}
              aria-describedby={describedBy}
              type="text"
              maxLength={14}
              readOnly={!codeEditable}
              disabled={readOnly}
              value={values.code}
              placeholder="AMOR27"
              onChange={(e) => set({ code: sanitizeCouponCode(e.target.value) })}
              onBlur={() => touch("code")}
              className={INPUT_CLS + " font-mono uppercase tracking-wide placeholder:normal-case"}
            />
          )}
        </Field>

        <Field label="Nombre de la campaña" error={errorFor("campaignName")}>
          {(id, describedBy) => (
            <input
              id={id}
              aria-describedby={describedBy}
              type="text"
              maxLength={CAMPAIGN_NAME_MAX}
              disabled={readOnly}
              value={values.campaignName}
              placeholder="Si lo dejas vacío, se usa el código"
              onChange={(e) => set({ campaignName: e.target.value })}
              onBlur={() => touch("campaignName")}
              className={INPUT_CLS}
            />
          )}
        </Field>

        <Field label="Descuento (%)" error={errorFor("percentage")}>
          {(id, describedBy) => (
            <input
              id={id}
              aria-describedby={describedBy}
              type="text"
              inputMode="numeric"
              maxLength={3}
              disabled={readOnly}
              value={values.percentage}
              placeholder="10"
              onChange={(e) => set({ percentage: e.target.value })}
              onBlur={() => touch("percentage")}
              className={INPUT_CLS + " tabular-nums"}
            />
          )}
        </Field>

        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Desde" error={errorFor("startsOn")}>
            {(id, describedBy) => (
              <input
                id={id}
                aria-describedby={describedBy}
                type="date"
                disabled={readOnly}
                value={values.startsOn}
                onChange={(e) => set({ startsOn: e.target.value })}
                onBlur={() => touch("startsOn")}
                className={INPUT_CLS + " tabular-nums"}
              />
            )}
          </Field>
          <Field label="Hasta" error={errorFor("endsOn")}>
            {(id, describedBy) => (
              <input
                id={id}
                aria-describedby={describedBy}
                type="date"
                disabled={readOnly}
                value={values.endsOn}
                onChange={(e) => set({ endsOn: e.target.value })}
                onBlur={() => touch("endsOn")}
                className={INPUT_CLS + " tabular-nums"}
              />
            )}
          </Field>
          <p className="col-span-2 -mt-1 text-[10.5px] leading-snug text-fg-faint">
            El último día cuenta completo (hora de Colombia)
          </p>
        </div>
      </div>

      <ProductsField
        values={values}
        readOnly={readOnly}
        products={products}
        loading={productsLoading}
        loadError={productsError ? apiErrorDetail(productsError) : null}
        error={errorFor("products")}
        onMode={(productsMode) => {
          touch("products");
          set({ productsMode });
        }}
        onToggle={toggleProduct}
      />

      {generalError ? (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-[11.5px] text-danger">
          {generalError}
        </p>
      ) : null}

      {readOnly ? null : (
        <div className="flex items-center justify-end gap-2">
          {mutation.isPending ? (
            <span className="mr-auto text-[11px] text-fg-muted">Guardando…</span>
          ) : null}
          {isNew && onCancel ? (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md px-3 py-1.5 text-[12px] font-semibold text-fg-muted hover:text-fg"
            >
              Cancelar
            </button>
          ) : null}
          {isNew ? (
            <>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() => submit("draft")}
                className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
              >
                Guardar borrador
              </button>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() => submit("active")}
                className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
              >
                Crear y activar
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={mutation.isPending}
              onClick={() => submit("active")}
              className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Guardar cambios
            </button>
          )}
        </div>
      )}
    </form>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error: string | undefined;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[11px] font-medium text-fg-muted">
        {label}
      </label>
      {children(id, error ? errorId : undefined)}
      {error ? (
        <span id={errorId} className="text-[10.5px] leading-snug text-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function ProductsField({
  values,
  readOnly,
  products,
  loading,
  loadError,
  error,
  onMode,
  onToggle,
}: {
  values: CouponFormValues;
  readOnly: boolean;
  products: { id: string; title: string }[];
  loading: boolean;
  /** El catálogo no cargó (D9): sin él no hay productos para elegir. */
  loadError: string | null;
  error: string | undefined;
  onMode: (mode: CouponFormValues["productsMode"]) => void;
  onToggle: (id: string) => void;
}) {
  const name = useId();
  const errorId = `${name}-error`;
  // Productos del cupón que el catálogo ya no trae: se siguen mostrando
  // (el backend los respeta) para no perderlos al guardar.
  const known = new Set(products.map((p) => p.id));
  const listed = [
    ...products,
    ...values.products.filter((id) => !known.has(id)).map((id) => ({ id, title: id })),
  ];
  return (
    <fieldset
      aria-describedby={error ? errorId : undefined}
      className="flex flex-col gap-1.5 rounded-md border border-line px-3 py-2"
    >
      <legend className="px-1 text-[11px] font-medium text-fg-muted">Productos</legend>
      <div className="flex gap-4">
        {(
          [
            { key: "all", label: "Todo el catálogo" },
            { key: "selected", label: "Productos elegidos" },
          ] as const
        ).map((o) => (
          <label key={o.key} className="flex items-center gap-1.5 text-[12px] text-fg">
            <input
              type="radio"
              name={name}
              disabled={readOnly}
              checked={values.productsMode === o.key}
              onChange={() => onMode(o.key)}
              className="accent-accent"
            />
            {o.label}
          </label>
        ))}
      </div>
      {values.productsMode === "selected" ? (
        <div className="flex max-h-48 flex-col gap-1 overflow-y-auto pt-1">
          {loading && listed.length === 0 ? (
            <span className="text-[11px] text-fg-faint">Cargando productos…</span>
          ) : null}
          {loadError ? (
            <span className="text-[11px] leading-snug text-danger">
              No se pudieron cargar los productos del catálogo: {loadError}
            </span>
          ) : null}
          {listed.map((p) => (
            <label key={p.id} className="flex items-center gap-2 text-[12px] text-fg-soft">
              <input
                type="checkbox"
                disabled={readOnly}
                checked={values.products.includes(p.id)}
                onChange={() => onToggle(p.id)}
                className="accent-accent"
              />
              {p.title}
            </label>
          ))}
        </div>
      ) : null}
      {error ? (
        <span id={errorId} className="text-[10.5px] leading-snug text-danger">
          {error}
        </span>
      ) : null}
    </fieldset>
  );
}
