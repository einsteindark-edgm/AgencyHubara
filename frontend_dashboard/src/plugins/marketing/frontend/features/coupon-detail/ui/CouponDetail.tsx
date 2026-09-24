/**
 * Detalle del cupón — panel central de la vista Cupones.
 *
 * Cabecera con estado + pausar/activar + borrar (solo borrador, confirmación
 * inline de dos pasos: regla #6). Si la central no sabe editar el cupón
 * (`manageable=false`, D7) se muestra el motivo y no hay acciones sobre
 * Medusa — el cupo por unidad sí se edita (vive en Hubara). El formulario de
 * datos lo compone el Page por `renderForm` (features no se importan entre
 * sí). Debajo: "Unidades con descuento" y el registro de cambios.
 *
 * Premortem: un refetch fallido NO borra el panel ni lo escrito (D5: el
 * error completo solo sin datos; con datos, un aviso). La edición del cupón
 * vive ACÁ y se le pasa al formulario (D6): se re-siembra solo cuando cambian
 * los datos editables (no al pausar/activar) y el error "quedó a medias"
 * sobrevive al re-sembrado.
 */

import { Fragment, useMemo, useState, type ReactNode } from "react";

import {
  COUPON_STATE_META,
  useCoupon,
  useCouponProducts,
  useDeleteCoupon,
  useSetCouponStatus,
  useUpdateCoupon,
  type Coupon,
  type CouponUpdateMutation,
} from "@plugins/marketing/frontend/entities/coupon";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

import { ChangesLog } from "./ChangesLog";
import { UnitsEditor } from "./UnitsEditor";

interface Props {
  couponId: string;
  /** El Page inyecta el formulario de edición (feature coupon-form) con la
   *  mutación de edición de ESTE detalle (su error sobrevive al re-sembrado). */
  renderForm: (coupon: Coupon, update: CouponUpdateMutation) => ReactNode;
  /** Tras borrar, el Page limpia la selección. */
  onDeleted: () => void;
}

export function CouponDetail({ couponId, renderForm, onDeleted }: Props) {
  const { data, error, refetch } = useCoupon(couponId);
  const update = useUpdateCoupon(couponId);
  const { data: products = [], error: productsError } = useCouponProducts();
  const productTitles = useMemo(
    () => new Map(products.map((p) => [p.id, p.title])),
    [products],
  );

  // D5: el error ocupa el panel SOLO si no hay datos; un refetch fallido con
  // datos deja el cupón (y lo que el operador escribió) y avisa arriba.
  if (!data) {
    return error ? (
      <p className="m-auto max-w-sm text-center text-[12px] text-danger">
        {apiErrorDetail(error)}
      </p>
    ) : (
      <p className="m-auto text-[12px] text-fg-muted">Cargando cupón…</p>
    );
  }

  const { coupon, units, changes } = data;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CouponHeader coupon={coupon} onDeleted={onDeleted} />

      {!coupon.manageable ? (
        <div className="shrink-0 border-b border-line bg-warn-soft px-5 py-2 text-[11.5px] font-medium text-warn">
          {coupon.unmanageableReason ?? "Este cupón se ve en solo lectura."}
        </div>
      ) : null}
      {error ? (
        <div
          role="status"
          className="shrink-0 border-b border-line bg-danger-soft px-5 py-2 text-[11.5px] font-medium text-danger"
        >
          No se pudo actualizar el cupón (lo que ves puede no estar al día): {apiErrorDetail(error)}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-5 py-4">
          <Section title="Datos del cupón">
            {/* Tras guardar (o una edición a medias) el formulario se
                re-siembra con lo que quedó en Medusa; el error de la edición
                vive en `update` (acá), así que sobrevive al re-sembrado. */}
            <Fragment key={formSignature(coupon)}>{renderForm(coupon, update)}</Fragment>
          </Section>

          <Section title="Unidades con descuento">
            {coupon.acceptsUnits && productsError ? (
              <p className="mb-2 text-[11.5px] leading-snug text-danger">
                No se pudieron cargar los productos del catálogo (sin la lista no se eligen
                producto, color y aroma): {apiErrorDetail(productsError)}
              </p>
            ) : null}
            {coupon.acceptsUnits ? (
              // Sin `key` por lo guardado: el editor sigue lo nuevo él mismo
              // y NO pisa un borrador con cambios (D7).
              <UnitsEditor
                coupon={coupon}
                units={units}
                products={products}
                onReload={() => void refetch()}
              />
            ) : (
              <p className="text-[11.5px] text-fg-faint">
                El cupo por unidad es solo para cupones de porcentaje.
              </p>
            )}
          </Section>

          <Section title="Registro de cambios">
            <ChangesLog changes={changes} productTitles={productTitles} />
          </Section>
        </div>
      </div>
    </div>
  );
}

/** Solo los datos EDITABLES: pausar/activar (estado) o un refetch que no los
 *  cambia no re-siembran el formulario ni borran lo que el operador escribió
 *  (D6). `status`/`manageable` los lee el formulario en vivo. */
function formSignature(c: Coupon): string {
  return JSON.stringify([
    c.code,
    c.campaignName,
    c.percentage,
    c.products,
    c.startsOn,
    c.endsOn,
  ]);
}

function CouponHeader({ coupon, onDeleted }: { coupon: Coupon; onDeleted: () => void }) {
  const meta = COUPON_STATE_META[coupon.state];
  const setStatus = useSetCouponStatus(coupon.promotionId);
  const remove = useDeleteCoupon(coupon.promotionId);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const toggle: { label: string; status: "active" | "inactive" } | null = !coupon.manageable
    ? null
    : coupon.state === "active" || coupon.state === "scheduled"
      ? { label: "Pausar", status: "inactive" }
      : coupon.state === "paused" || coupon.state === "draft"
        ? { label: "Activar", status: "active" }
        : null;
  const canDelete = coupon.manageable && coupon.status === "draft";
  const actionError = setStatus.error ?? remove.error;

  return (
    <header className="shrink-0 border-b border-line px-5 py-3">
      <div className="flex items-center gap-3">
        <h1 className="font-mono text-[17px] font-bold tracking-wide text-fg">{coupon.code}</h1>
        {coupon.percentage !== null ? (
          <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[11px] font-bold text-accent-fg">
            -{coupon.percentage}%
          </span>
        ) : null}
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px] font-semibold ${TONE_CLS[meta.tone]}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          {meta.label}
        </span>
        <span className="ml-auto flex items-center gap-2">
          {toggle ? (
            <button
              type="button"
              disabled={setStatus.isPending}
              onClick={() => setStatus.mutate(toggle.status)}
              className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
            >
              {toggle.label}
            </button>
          ) : null}
          {canDelete && !confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="rounded-md border border-danger/40 px-3 py-1.5 text-[12px] font-semibold text-danger hover:bg-danger-soft"
            >
              Borrar
            </button>
          ) : null}
        </span>
      </div>
      <p className="mt-0.5 text-[11.5px] text-fg-muted">
        {coupon.campaignName ?? "Sin campaña"}
        {coupon.endsOnLabel ? ` · válido hasta el ${coupon.endsOnLabel}` : ""}
      </p>

      {canDelete && confirmDelete ? (
        <div className="mt-2 flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-[11.5px] text-danger">
          <span>¿Borrar el cupón {coupon.code}? Se borra también en Medusa.</span>
          <button
            type="button"
            disabled={remove.isPending}
            onClick={() => remove.mutate(undefined, { onSuccess: onDeleted })}
            className="ml-auto rounded-md bg-danger px-2.5 py-1 text-[11.5px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Confirmar borrado
          </button>
          <button
            type="button"
            onClick={() => setConfirmDelete(false)}
            className="rounded-md px-2 py-1 text-[11.5px] font-semibold text-fg-muted hover:text-fg"
          >
            Cancelar
          </button>
        </div>
      ) : null}

      {actionError ? (
        <p role="alert" className="mt-2 text-[11.5px] text-danger">
          {apiErrorDetail(actionError)}
        </p>
      ) : null}
    </header>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line px-4 py-3">
      <h2 className="mb-2.5 text-[12.5px] font-semibold tracking-tight text-fg">{title}</h2>
      {children}
    </section>
  );
}
