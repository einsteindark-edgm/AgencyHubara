/**
 * `AdsSection` — la Page del plugin ads.
 *
 * Compone el dashboard de Ads Analytics: sidebar de campañas Meta (izq),
 * canvas con header de KPIs + embudo + distribución + serie diaria + tabla
 * de conversaciones atribuidas (centro), e inspector de detalle (der).
 *
 * Plugin frontend-only — los datos (`ads-campaign`) viven en `entities/`. El
 * plugin no aporta backend ni worker; cuando llegue Meta sync, basta swappear
 * el queryFn de los hooks (la firma no cambia).
 *
 * Recibe el "envelope" estándar de props del shell (showSidebar/showInspector);
 * la selección de campaña es state local del plugin — no cross-feature, no
 * sobrevive a switch de section. Si esto cambia (p.ej. deep-link via route),
 * promover a `selectedCampaignId` en `Dashboard.tsx`.
 */

import { useMemo, useState } from "react";

import { usePluginHost } from "@/shared/lib";

import {
  DEFAULT_ADS_SELECTION,
  selectionToParams,
  useAdsCampaigns,
  useAttributedConversations,
  useDailySeries,
  type AdsRangeSelection,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsCampaignsList } from "@plugins/ads/frontend/features/ads-campaigns-list";
import { AdsOverviewHeader } from "@plugins/ads/frontend/features/ads-overview-header";
import { AdsFunnel } from "@plugins/ads/frontend/features/ads-funnel";
import { AdsStateDistribution } from "@plugins/ads/frontend/features/ads-state-distribution";
import { AdsDailyTrend } from "@plugins/ads/frontend/features/ads-daily-trend";
import { AdsAttributedTable } from "@plugins/ads/frontend/features/ads-attributed-table";
import { AdsInspector } from "@plugins/ads/frontend/features/ads-inspector";

export function AdsSection() {
  // F7: el chrome del shell llega por el PluginHost (contrato genérico) — el
  // shell ya no pasa props por plugin.
  const { showSidebar, showInspector } = usePluginHost();
  // Ventana temporal — default preset 30d (acotada) para que el cómputo del
  // backend no escale con todo el historial. El operador puede ampliar a Total
  // o fijar un rango exacto (fecha inicio → fecha fin) desde el header.
  const [selection, setSelection] = useState<AdsRangeSelection>(
    DEFAULT_ADS_SELECTION,
  );
  const params = selectionToParams(selection);
  // El gráfico diario no puede tener infinitas columnas: un preset manda `days`
  // (Total → cap 90 del backend); un rango custom manda from/to y el backend lo
  // clampa a 90.
  const dailyParams: AdsWindowParams =
    selection.kind === "custom"
      ? params
      : { days: params.days ?? 90, from: null, to: null };

  const { data: campaigns = [] } = useAdsCampaigns(params);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Default a la primera campaña activa una vez que llegan los datos. Si la
  // campaña seleccionada cae FUERA de la ventana (no está en la lista
  // filtrada), caemos a la primera disponible en vez de mostrar vacío. Memo
  // garantiza id estable mientras `campaigns` no cambie de identidad.
  const fallbackId = useMemo(() => {
    const inList = (id: string | null) =>
      !!id && campaigns.some((c) => c.id === id);
    if (inList(selectedId)) return selectedId;
    return campaigns.find((c) => c.status === "active")?.id ?? campaigns[0]?.id ?? null;
  }, [campaigns, selectedId]);

  const campaign = useMemo(
    () => campaigns.find((c) => c.id === fallbackId) ?? null,
    [campaigns, fallbackId],
  );

  const { data: attributed = [] } = useAttributedConversations(
    campaign?.id ?? "",
    params,
  );
  const { data: daily = [] } = useDailySeries(campaign?.id ?? "", dailyParams);

  if (!campaign) {
    // Empty-state: aún sin campañas cargadas. Idéntico al patrón de eta-chat.
    return (
      <main className="ads-canvas">
        <div className="ads-empty">Sin campañas para mostrar.</div>
      </main>
    );
  }

  return (
    <>
      {showSidebar && (
        <AdsCampaignsList
          campaigns={campaigns}
          selected={campaign.id}
          onSelect={setSelectedId}
        />
      )}
      <main className="ads-canvas">
        <AdsOverviewHeader
          campaign={campaign}
          selection={selection}
          onSelectionChange={setSelection}
        />
        <div className="ads-body">
          <AdsFunnel campaign={campaign} />
          <AdsStateDistribution campaign={campaign} />
          <AdsDailyTrend series={daily} />
          <AdsAttributedTable rows={attributed} />
        </div>
      </main>
      {showInspector && <AdsInspector campaign={campaign} />}
    </>
  );
}

export default AdsSection;
