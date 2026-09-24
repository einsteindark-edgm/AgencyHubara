/**
 * Detalle del cupón — panel central de la vista Cupones.
 *
 * Cabecera con estado + pausar/activar + borrar (solo borrador, confirmación
 * inline de dos pasos: regla #6). Si la central no sabe editar el cupón
 * (`manageable=false`, D7) se muestra el motivo y no hay acciones sobre
 * Medusa — el cupo por unidad sí se edita (vive en Hubara). El formulario de
 * datos lo compone el Page por `renderForm` (features no se importan entre
 * sí). Debajo: "Unidades con descuento" y el registro de cambios.
 */

import { Fragment, useState, type ReactNode } from "react";

import {
  COUPON_STATE_META,
  useCoupon,
  useCouponProducts,
  useDeleteCoupon,
  useSetCouponStatus,
  type Coupon,
  type CouponUnits,
} from "@plugins/marketing/frontend/entities/coupon";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

import { ChangesLog } from "./ChangesLog";
import { UnitsEditor } from "./UnitsEditor";

interface Props {
  couponId: string;
  /** El Page inyecta el formulario de edición (feature coupon-form). */
  renderForm: (coupon: Coupon) => ReactNode;
  /** Tras borrar, el Page limpia la selección. */
  onDeleted: () => void;
}

export function CouponDetail({ couponId, renderForm, onDeleted }: Props) {
  const { data, isPending, error } = useCoupon(couponId);
  const { data: products = [] } = useCouponProducts();

  if (error) {
    return (
      <p className="m-auto max-w-sm text-center text-[12px] text-danger">
        {apiErrorDetail(error)}
      </p>
    );
  }
  if (isPending || !data) {
    return <p className="m-auto text-[12px] text-fg-muted">Cargando cupón…</p>;
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

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-5 py-4">
          <Section title="Datos del cupón">
            {/* Tras guardar (o una edición a medias) el formulario se
                re-siembra con lo que quedó en Medusa. */}
            <Fragment key={formSignature(coupon)}>{renderForm(coupon)}</Fragment>
          </Section>

          <Section title="Unidades con descuento">
            {coupon.acceptsUnits ? (
              <UnitsEditor
                key={unitsSignature(units)}
                coupon={coupon}
                units={units}
                products={products}
              />
            ) : (
              <p className="text-[11.5px] text-fg-faint">
                El cupo por unidad es solo para cupones de porcentaje.
              </p>
            )}
          </Section>

          <Section title="Registro de cambios">
            <ChangesLog changes={changes} />
          </Section>
        </div>
      </div>
    </div>
  );
}

function formSignature(c: Coupon): string {
  return JSON.stringify([
    c.code,
    c.campaignName,
    c.percentage,
    c.products,
    c.startsOn,
    c.endsOn,
    c.status,
    c.manageable,
  ]);
}

/** Cambia cuando cambian las filas guardadas → el editor se re-siembra. */
function unitsSignature(units: CouponUnits): string {
  return JSON.stringify([
    units.showUnitsLeft,
    units.rows.map((r) => [r.id, r.productId, r.color, r.aroma, r.units]),
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
