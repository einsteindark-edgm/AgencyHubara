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

import {
  useAdsCampaigns,
  useAttributedConversations,
  useDailySeries,
} from "@/entities/ads-campaign";

import { AdsCampaignsList } from "@plugins/ads/frontend/features/ads-campaigns-list";
import { AdsOverviewHeader } from "@plugins/ads/frontend/features/ads-overview-header";
import { AdsFunnel } from "@plugins/ads/frontend/features/ads-funnel";
import { AdsStateDistribution } from "@plugins/ads/frontend/features/ads-state-distribution";
import { AdsDailyTrend } from "@plugins/ads/frontend/features/ads-daily-trend";
import { AdsAttributedTable } from "@plugins/ads/frontend/features/ads-attributed-table";
import { AdsInspector } from "@plugins/ads/frontend/features/ads-inspector";

export interface AdsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function AdsSection({ showSidebar, showInspector }: AdsSectionProps) {
  const { data: campaigns = [] } = useAdsCampaigns();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Default a la primera campaña activa una vez que llegan los datos. Memo
  // garantiza id estable mientras `campaigns` no cambie de identidad.
  const fallbackId = useMemo(() => {
    if (selectedId) return selectedId;
    return campaigns.find((c) => c.status === "active")?.id ?? campaigns[0]?.id ?? null;
  }, [campaigns, selectedId]);

  const campaign = useMemo(
    () => campaigns.find((c) => c.id === fallbackId) ?? null,
    [campaigns, fallbackId],
  );

  const { data: attributed = [] } = useAttributedConversations(campaign?.id ?? "");
  const { data: daily = [] } = useDailySeries(campaign?.id ?? "");

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
        <AdsOverviewHeader campaign={campaign} />
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
