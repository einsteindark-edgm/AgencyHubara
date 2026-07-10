/**
 * KPIs de Meta de la CAMPAÑA SELECCIONADA (pedido 2026-07-09): gasto,
 * impresiones, clicks, conversaciones, CPC y costo/conv dentro del canvas —
 * los datos vienen del merge del endpoint de campañas, así que responden a la
 * ventana de fecha del header (days/from/to). Reemplaza al panel estático
 * (`MetaInsightsPanel`) que pintaba TODAS las campañas fijas en 30d.
 *
 * Gestión pausar/activar por campaña (confirm inline de dos pasos, regla 6)
 * cuando el token tiene `ads_management`. Derivados (CPC, costo/conv) se
 * computan en render (regla 5); estado de la mutation se deriva de la mutation.
 */

import { useState } from "react";

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";
import {
  useMetaConnection,
  useSetCampaignStatus,
} from "@plugins/ads/frontend/entities/meta-connection";

const cop = new Intl.NumberFormat("es-CO", {
  style: "currency",
  currency: "COP",
  maximumFractionDigits: 0,
});
const num = new Intl.NumberFormat("es-CO");

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-line px-3 py-2">
      <span className="text-[11px] uppercase tracking-wide text-fg-muted">{label}</span>
      <span className="text-sm font-semibold text-fg">{value}</span>
    </div>
  );
}

export function CampaignMetaKpis({ campaign }: { campaign: AdsCampaign }) {
  const { data: conn } = useMetaConnection();
  const setStatus = useSetCampaignStatus();
  const [confirming, setConfirming] = useState(false);

  const hasMetaData =
    campaign.spend !== null ||
    campaign.impressions !== null ||
    campaign.clicks !== null ||
    campaign.conversationsStarted !== null;
  if (!campaign.metaCampaignId || !hasMetaData) return null;

  const spend = campaign.spend;
  const clicks = campaign.clicks;
  const convs = campaign.conversationsStarted;
  const cpc = spend !== null && clicks !== null && clicks > 0 ? spend / clicks : null;
  const costPerConv = spend !== null && convs !== null && convs > 0 ? spend / convs : null;

  const canManage = (conn?.canManage ?? false) && campaign.status !== null;
  const nextStatus = campaign.status === "active" ? "PAUSED" : "ACTIVE";
  const actionLabel = campaign.status === "active" ? "Pausar" : "Activar";

  return (
    <section className="mx-4 my-2">
      <header className="flex items-center justify-between px-1 pb-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Meta · métricas de la ventana
        </h3>
        {canManage &&
          (confirming ? (
            <span className="flex items-center gap-2">
              <button
                type="button"
                disabled={setStatus.isPending}
                onClick={() =>
                  setStatus.mutate(
                    { campaignId: campaign.metaCampaignId!, status: nextStatus },
                    { onSettled: () => setConfirming(false) },
                  )
                }
                className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
              >
                {setStatus.isPending ? "Aplicando…" : `Confirmar ${actionLabel.toLowerCase()}`}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded px-2 py-1 text-xs text-fg-muted hover:text-fg"
              >
                Cancelar
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="rounded border border-line px-2 py-1 text-xs text-fg-muted hover:text-fg"
            >
              {actionLabel} campaña
            </button>
          ))}
      </header>
      <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
        <Kpi label="Gasto" value={spend !== null ? cop.format(spend) : "—"} />
        <Kpi
          label="Impresiones"
          value={campaign.impressions !== null ? num.format(campaign.impressions) : "—"}
        />
        <Kpi label="Clicks" value={clicks !== null ? num.format(clicks) : "—"} />
        <Kpi label="Conversaciones" value={convs !== null ? num.format(convs) : "—"} />
        <Kpi label="CPC" value={cpc !== null ? cop.format(cpc) : "—"} />
        <Kpi label="Costo/conv" value={costPerConv !== null ? cop.format(costPerConv) : "—"} />
      </div>
    </section>
  );
}
