/**
 * `MarketingSection` — la Page del plugin marketing.
 *
 * Selector `Campañas | Cupones` arriba del sidebar izquierdo (estado local de
 * la Page). Cada vista compone sus 3 paneles y conserva SU selección en el
 * PluginHost (regla #4) con claves distintas:
 *  - Campañas: sidebar de campañas, builder de 6 pasos e inspector
 *    Preview/Validación (`useSelection("marketing")`). `key={campaign.id}` en
 *    el builder resetea su draft local al cambiar de campaña.
 *  - Cupones (central de cupones): sidebar de cupones, detalle o formulario
 *    de alta e inspector de ventas (`useSelection("marketing-coupons")`).
 * El atajo "Crear cupón" del constructor salta a Cupones con el formulario:
 * las features no se importan entre sí, la Page orquesta.
 */

import { useMemo, useState, type ReactNode } from "react";

import { usePluginHost, useSelection } from "@/shared/sdk";
import { Icon } from "@/shared/ui";

import {
  useCampaigns,
  useCreateCampaign,
} from "@plugins/marketing/frontend/entities/campaign";
import { useCoupons } from "@plugins/marketing/frontend/entities/coupon";
import { CampaignsList } from "@plugins/marketing/frontend/features/campaigns-list";
import { CampaignBuilder } from "@plugins/marketing/frontend/features/campaign-builder";
import { CampaignInspector } from "@plugins/marketing/frontend/features/campaign-inspector";
import { CouponDetail } from "@plugins/marketing/frontend/features/coupon-detail";
import { CouponForm } from "@plugins/marketing/frontend/features/coupon-form";
import { CouponSalesInspector } from "@plugins/marketing/frontend/features/coupon-sales";
import { CouponsList } from "@plugins/marketing/frontend/features/coupons-list";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";

type View = "campaigns" | "coupons";

/** Selección de la vista Cupones en el PluginHost (la de campañas es "marketing"). */
const COUPON_SELECTION_KEY = "marketing-coupons";

export function MarketingSection() {
  const [view, setView] = useState<View>("campaigns");
  const [creatingCoupon, setCreatingCoupon] = useState(false);

  const switcher = (
    <div className="seg self-start" role="tablist" aria-label="Vista de marketing">
      {(
        [
          { key: "campaigns", label: "Campañas" },
          { key: "coupons", label: "Cupones" },
        ] as const
      ).map((v) => (
        <button
          key={v.key}
          type="button"
          role="tab"
          aria-selected={view === v.key}
          className={view === v.key ? "on" : ""}
          onClick={() => setView(v.key)}
        >
          {v.label}
        </button>
      ))}
    </div>
  );

  if (view === "coupons") {
    return (
      <CouponsView
        switcher={switcher}
        creating={creatingCoupon}
        setCreating={setCreatingCoupon}
      />
    );
  }
  return (
    <CampaignsView
      switcher={switcher}
      onCreateCoupon={() => {
        setCreatingCoupon(true);
        setView("coupons");
      }}
    />
  );
}

function CampaignsView({
  switcher,
  onCreateCoupon,
}: {
  switcher: ReactNode;
  onCreateCoupon: () => void;
}) {
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedId, setSelectedId] = useSelection("marketing");
  const { data: campaigns = [] } = useCampaigns();
  // La creación desde el empty state es orquestación page-level (la lista
  // trae su propio botón "Nueva campaña" para el flujo normal).
  const create = useCreateCampaign();

  // Fallback en render: si la selección no existe (aún nada seleccionado o
  // campaña borrada), cae a la primera de la lista — nunca un panel vacío
  // teniendo campañas.
  const campaign = useMemo(
    () => campaigns.find((c) => c.id === selectedId) ?? campaigns[0] ?? null,
    [campaigns, selectedId],
  );

  return (
    <>
      {showSidebar && (
        <CampaignsList
          header={switcher}
          campaigns={campaigns}
          selectedId={campaign?.id ?? null}
          onSelect={setSelectedId}
          onCreated={setSelectedId}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-canvas">
        {campaign ? (
          <CampaignBuilder
            key={campaign.id}
            campaign={campaign}
            onCreateCoupon={onCreateCoupon}
          />
        ) : (
          <div className="m-auto flex max-w-sm flex-col items-center gap-3 text-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/15 text-accent-fg">
              <Icon.send />
            </span>
            <h1 className="text-[15px] font-bold tracking-tight text-fg">
              Campañas de WhatsApp
            </h1>
            <p className="text-[12.5px] leading-relaxed text-fg-muted">
              Arma una campaña con descuento o lanzamiento, elige la audiencia
              por segmentos y envíala por el template aprobado de WhatsApp.
            </p>
            <button
              type="button"
              disabled={create.isPending}
              onClick={() =>
                create.mutate(undefined, { onSuccess: (c) => setSelectedId(c.id) })
              }
              className="rounded-md bg-accent px-4 py-2 text-[12.5px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Crear la primera campaña
            </button>
            {create.error ? (
              <p className="text-[11.5px] text-danger">
                {apiErrorDetail(create.error)}
              </p>
            ) : null}
          </div>
        )}
      </main>

      {showInspector && campaign && <CampaignInspector campaign={campaign} />}
    </>
  );
}

function CouponsView({
  switcher,
  creating,
  setCreating,
}: {
  switcher: ReactNode;
  creating: boolean;
  setCreating: (v: boolean) => void;
}) {
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedId, setSelectedId] = useSelection(COUPON_SELECTION_KEY);
  const { data: coupons = [], error } = useCoupons();

  // Mismo fallback que campañas: sin selección válida, el primero.
  const coupon = useMemo(
    () => coupons.find((c) => c.promotionId === selectedId) ?? coupons[0] ?? null,
    [coupons, selectedId],
  );

  const select = (id: string) => {
    setCreating(false);
    setSelectedId(id);
  };

  return (
    <>
      {showSidebar && (
        <CouponsList
          header={switcher}
          coupons={coupons}
          selectedId={creating ? null : (coupon?.promotionId ?? null)}
          onSelect={select}
          onNew={() => setCreating(true)}
          notice={error ? apiErrorDetail(error) : null}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-canvas">
        {creating ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-5 py-4">
              <h1 className="text-[17px] font-bold tracking-tight text-fg">Nuevo cupón</h1>
              <CouponForm onCreated={select} onCancel={() => setCreating(false)} />
            </div>
          </div>
        ) : coupon ? (
          <CouponDetail
            key={coupon.promotionId}
            couponId={coupon.promotionId}
            renderForm={(c) => <CouponForm coupon={c} />}
            onDeleted={() => setSelectedId(null)}
          />
        ) : (
          <div className="m-auto flex max-w-sm flex-col items-center gap-3 text-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/15 text-accent-fg">
              <Icon.tag />
            </span>
            <h1 className="text-[15px] font-bold tracking-tight text-fg">
              Central de cupones
            </h1>
            <p className="text-[12.5px] leading-relaxed text-fg-muted">
              Crea cupones de porcentaje sin entrar a Medusa y ponles un cupo por
              unidad: producto, color y aroma.
            </p>
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="rounded-md bg-accent px-4 py-2 text-[12.5px] font-semibold text-white hover:opacity-90"
            >
              Crear el primer cupón
            </button>
          </div>
        )}
      </main>

      {showInspector && !creating && coupon && (
        <CouponSalesInspector couponId={coupon.promotionId} />
      )}
    </>
  );
}

export default MarketingSection;
