/**
 * Paso 2 — Descuento / Producto según el objetivo:
 *  - todo goal salvo launch → el cupón se ELIGE de la central de cupones
 *    (solo de porcentaje, activos y programados). El % y el "válido hasta" salen del cupón
 *    (solo lectura) y se guardan en la campaña al elegirlo; el backend los
 *    vuelve a copiar del cupón al enviar. Atajo "Crear cupón": el Page abre
 *    la vista Cupones con el formulario nuevo.
 *  - todo goal → carrusel de 2..10 productos del catálogo: es LA forma de
 *    elegir productos (el selector de producto único se retiró — lo que se
 *    envía son las tarjetas del carrusel)
 */

import { Icon } from "@/shared/ui";

import { goalUsesDiscount } from "@plugins/marketing/frontend/entities/campaign";
import {
  isCouponPickable,
  useCoupons,
} from "@plugins/marketing/frontend/entities/coupon";

import type { CampaignDraft } from "../model/draft";
import { CarouselPicker } from "./CarouselPicker";

interface Props {
  draft: CampaignDraft;
  editable: boolean;
  onPatch: (p: Partial<CampaignDraft>) => void;
  onCommit: (p?: Partial<CampaignDraft>) => void;
  /** Atajo a la central: el Page cambia a la vista Cupones (alta). */
  onCreateCoupon?: () => void;
}

export function OfferStep({ draft, editable, onCommit, onCreateCoupon }: Props) {
  const usesDiscount = goalUsesDiscount(draft.goal);
  // Cupones de la central: el mismo código que el bot valida con
  // `apply_coupon` — la campaña solo promete un cupón que rige o va a regir.
  const { data: coupons, error: couponsError } = useCoupons(usesDiscount);
  const pickable = (coupons ?? []).filter(isCouponPickable);
  const picked = pickable.find((c) => c.code === draft.couponCode) ?? null;
  // Solo con la lista REAL de la central (si no respondió, no sabemos).
  const couponNotPickable = draft.couponCode !== "" && coupons !== undefined && picked === null;
  // Un cupón de monto fijo (creado en Medusa) rige pero no se puede anunciar.
  const fixedAmount =
    couponNotPickable &&
    (coupons ?? []).some((c) => c.code === draft.couponCode && c.percentage === null);

  const pick = (code: string) => {
    const c = pickable.find((p) => p.code === code);
    onCommit(
      c
        ? { couponCode: c.code, percent: c.percentage ?? 0, validUntil: c.endsOnLabel ?? "" }
        : { couponCode: "", percent: 0, validUntil: "" },
    );
  };

  if (draft.goal === "") {
    return (
      <p className="text-[11.5px] text-fg-faint">
        Elige primero el objetivo de la campaña (paso 1).
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {usesDiscount ? (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-end gap-2">
            <label className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="text-[11px] font-medium text-fg-muted">Cupón</span>
              <select
                disabled={!editable}
                value={picked ? picked.code : ""}
                onChange={(e) => pick(e.target.value)}
                className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60"
              >
                <option value="">
                  {pickable.length > 0 ? "Elige un cupón" : "No hay cupones activos ni programados"}
                </option>
                {pickable.map((c) => (
                  <option key={c.promotionId} value={c.code}>
                    {c.code}
                    {c.percentage !== null ? ` · ${c.percentage}%` : ""}
                    {c.state === "scheduled" ? " · programado" : ""}
                  </option>
                ))}
              </select>
            </label>
            {onCreateCoupon ? (
              <button
                type="button"
                disabled={!editable}
                onClick={onCreateCoupon}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line px-2.5 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
              >
                <Icon.plus />
                Crear cupón
              </button>
            ) : null}
          </div>

          {picked ? (
            <span className="text-[11.5px] text-fg-soft">
              {picked.percentage !== null ? `${picked.percentage}%` : "Descuento"}
              {picked.endsOnLabel ? ` · válido hasta el ${picked.endsOnLabel}` : ""}
            </span>
          ) : null}
          {couponNotPickable ? (
            <span role="alert" className="text-[10.5px] leading-snug text-warn">
              {fixedAmount
                ? `${draft.couponCode} es de monto fijo: la campaña solo puede anunciar cupones de porcentaje y el envío se bloqueará. Elige otro.`
                : `${draft.couponCode} no está activo ni programado en la central de cupones: el cliente que lo escriba no recibirá el descuento y el envío se bloqueará. Elige otro o actívalo en Cupones.`}
            </span>
          ) : null}
          {couponsError ? (
            <span className="text-[10.5px] text-warn">
              No pude leer los cupones de la central ahora mismo — reintenta en un momento.
            </span>
          ) : null}
        </div>
      ) : (
        <p className="text-[11.5px] text-fg-faint">
          Un lanzamiento no lleva descuento — el mensaje invita a responder.
        </p>
      )}

      <CarouselPicker
        handles={draft.carouselHandles}
        editable={editable}
        onChange={(carouselHandles) => onCommit({ carouselHandles })}
      />
    </div>
  );
}
