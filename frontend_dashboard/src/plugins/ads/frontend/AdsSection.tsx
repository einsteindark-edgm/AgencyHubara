/**
 * `AdsSection` — la Page del plugin ads.
 *
 * Compone el dashboard de Ads Analytics: sidebar de campañas Meta (izq),
 * canvas con header de KPIs + embudo + distribución + serie diaria + tabla
 * de conversaciones atribuidas (centro), e inspector de detalle (der).
 *
 * El plugin tiene backend propio (`/api/ads`): campañas derivadas del vault +
 * el buzón de análisis con IA (`/api/ads/analysis/*`) que dispara el pod
 * `ads-analytics` de GraphAgents. Los datos viven en `entities/` (`ads-campaign`,
 * `ad-analysis-run`). El botón "Analizar con IA" abre ese buzón en un modal.
 *
 * Recibe el "envelope" estándar de props del shell (showSidebar/showInspector);
 * la selección de campaña es state local del plugin — no cross-feature, no
 * sobrevive a switch de section. Si esto cambia (p.ej. deep-link via route),
 * promover a `selectedCampaignId` en `Dashboard.tsx`.
 */

import { useMemo, useState, type ReactNode } from "react";

import { usePluginHost } from "@/shared/lib";
import { Icon } from "@/shared/ui";

import {
  DEFAULT_ADS_SELECTION,
  selectionToParams,
  useAdsCampaigns,
  useAttributedConversations,
  useDailySeries,
  type AdsRangeSelection,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";
// El run de análisis (entity propia del plugin). La Page lee el record con
// `useRun` para decidir si monta el panel HITL — igual que el resto de los
// datos de Ads, vive en `entities/`.
import { useRun } from "@plugins/ads/frontend/entities/ad-analysis-run";

import { AdsCampaignsList } from "@plugins/ads/frontend/features/ads-campaigns-list";
import { AdsOverviewHeader } from "@plugins/ads/frontend/features/ads-overview-header";
import { AdsFunnel } from "@plugins/ads/frontend/features/ads-funnel";
import { AdsStateDistribution } from "@plugins/ads/frontend/features/ads-state-distribution";
import { AdsDailyTrend } from "@plugins/ads/frontend/features/ads-daily-trend";
import { AdsAttributedTable } from "@plugins/ads/frontend/features/ads-attributed-table";
import { AdsInspector } from "@plugins/ads/frontend/features/ads-inspector";
// Las 3 features del buzón de análisis con IA. FSD: la Page es el único punto que
// las compone — `trigger-run` no importa a `run-result` ni a `hitl-decision`
// (cross-feature prohibido).
import { TriggerRun } from "@plugins/ads/frontend/features/trigger-run";
import { RunResult } from "@plugins/ads/frontend/features/run-result";
import { HitlDecision } from "@plugins/ads/frontend/features/hitl-decision";

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

  // ── Buzón de análisis con IA ─────────────────────────────────────────────
  // `activeRunId` es state LOCAL de la Page (orquestación page-level, regla #3):
  // el modal abre/cierra sin tocar el PluginHost; al cerrar NO se pierde el run.
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  // Record del run activo — para decidir si montamos el panel HITL y con qué
  // contexto. Comparte el query key con `RunResult` (TanStack dedupe): una sola
  // request, una sola suscripción al stream.
  const { data: run } = useRun(activeRunId);
  const decision =
    run?.status === "awaiting_approval" && activeRunId ? (
      <HitlDecision runId={activeRunId} awaiting={run.awaiting} />
    ) : undefined;

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
        {/* Acción del canvas: abre el buzón de análisis con IA (modal propio,
            no diálogo nativo — regla #6). Visible arriba de todo. */}
        <div className="flex items-center justify-end gap-2 px-4 pt-3">
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-fg hover:opacity-90"
            onClick={() => setAnalysisOpen(true)}
          >
            <Icon.bot />
            Analizar con IA
          </button>
        </div>
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

      {analysisOpen && (
        <AnalysisModal onClose={() => setAnalysisOpen(false)}>
          {/* Disparador a la izquierda (scroll propio), resultado en vivo a la
              derecha. El page es el único que une las 3 features (cross-feature
              prohibido). */}
          <div className="flex min-h-0 shrink-0 overflow-y-auto border-r border-line">
            <TriggerRun onRunStarted={setActiveRunId} />
          </div>
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <RunResult runId={activeRunId} decisionSlot={decision} />
          </div>
        </AnalysisModal>
      )}
    </>
  );
}

/**
 * Modal overlay propio del buzón de análisis (NO `<dialog>`/`window.confirm` —
 * regla #6: nativos no son confiables en los webviews de Tauri). Backdrop fijo +
 * panel centrado; la ✕ sólo oculta el modal (el run sigue vivo en la cache, así
 * que reabrir lo muestra de nuevo). Cierra también con click en el backdrop.
 */
interface AnalysisModalProps {
  onClose: () => void;
  children: ReactNode;
}

function AnalysisModal({ onClose, children }: AnalysisModalProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Análisis con IA"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-line bg-canvas shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex items-center gap-2 text-fg">
            <span className="text-accent">
              <Icon.bot />
            </span>
            <span className="text-sm font-semibold">Análisis con IA</span>
          </div>
          <button
            type="button"
            className="rounded-md p-1 text-fg-muted hover:bg-line hover:text-fg"
            onClick={onClose}
            aria-label="Cerrar"
          >
            <Icon.x />
          </button>
        </header>
        <div className="flex min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    </div>
  );
}

export default AdsSection;
