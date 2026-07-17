/**
 * Paso 2 — Descuento / Producto según el objetivo:
 *  - discount_product | launch → picker de producto del catálogo
 *  - todo goal salvo launch → porcentaje (0-100), cupón (uppercase, max 14)
 *    y vigencia (texto libre "15 de junio")
 */

import {
  goalNeedsProduct,
  goalUsesDiscount,
} from "@plugins/marketing/frontend/entities/campaign";

import type { CampaignDraft } from "../model/draft";
import { ProductPicker } from "./ProductPicker";

interface Props {
  draft: CampaignDraft;
  editable: boolean;
  onPatch: (p: Partial<CampaignDraft>) => void;
  onCommit: (p?: Partial<CampaignDraft>) => void;
}

export function OfferStep({ draft, editable, onPatch, onCommit }: Props) {
  const needsProduct = goalNeedsProduct(draft.goal);
  const usesDiscount = goalUsesDiscount(draft.goal);

  if (draft.goal === "") {
    return (
      <p className="text-[11.5px] text-fg-faint">
        Elegí primero el objetivo de la campaña (paso 1).
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {needsProduct ? (
        <ProductPicker
          value={draft.productHandle}
          editable={editable}
          onPick={(handle) => onCommit({ productHandle: handle })}
        />
      ) : null}

      {usesDiscount ? (
        <div className="grid grid-cols-3 gap-2.5 max-[900px]:grid-cols-1">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-medium text-fg-muted">Porcentaje</span>
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                min={0}
                max={100}
                disabled={!editable}
                value={draft.percent}
                onChange={(e) => {
                  const n = Math.max(0, Math.min(100, Number(e.target.value) || 0));
                  onPatch({ percent: n });
                }}
                onBlur={() => onCommit()}
                className="w-full rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] tabular-nums text-fg outline-none focus:border-accent disabled:opacity-60"
              />
              <span className="text-[12px] text-fg-muted">%</span>
            </div>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-medium text-fg-muted">
              Código de cupón
            </span>
            <input
              type="text"
              maxLength={14}
              disabled={!editable}
              value={draft.couponCode}
              placeholder="PAPA20"
              onChange={(e) => onPatch({ couponCode: e.target.value.toUpperCase() })}
              onBlur={() => onCommit()}
              className="w-full rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] uppercase tracking-wide text-fg outline-none focus:border-accent disabled:opacity-60 placeholder:normal-case placeholder:text-fg-faint"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-medium text-fg-muted">Válido hasta</span>
            <input
              type="text"
              maxLength={60}
              disabled={!editable}
              value={draft.validUntil}
              placeholder="15 de junio"
              onChange={(e) => onPatch({ validUntil: e.target.value })}
              onBlur={() => onCommit()}
              className="w-full rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60 placeholder:text-fg-faint"
            />
          </label>
        </div>
      ) : (
        <p className="text-[11.5px] text-fg-faint">
          Un lanzamiento no lleva descuento — el mensaje invita a responder.
        </p>
      )}
    </div>
  );
}
