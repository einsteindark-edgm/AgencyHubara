/**
 * Plugin `ads` — barrel del frontend.
 *
 * Exporta `default` (AdsSection) que el shell consume via lazy import desde
 * el registry. Plus named re-exports de cada feature para tests y back-compat.
 */
export { default, AdsSection } from "./AdsSection";

export { AdsCampaignsList } from "@plugins/ads/frontend/features/ads-campaigns-list";
export { AdsOverviewHeader } from "@plugins/ads/frontend/features/ads-overview-header";
export { AdsFunnel } from "@plugins/ads/frontend/features/ads-funnel";
export { AdsStateDistribution } from "@plugins/ads/frontend/features/ads-state-distribution";
export { AdsDailyTrend } from "@plugins/ads/frontend/features/ads-daily-trend";
export {
  AdsAttributedTable,
  ATTRIBUTED_STATE_FILTERS,
  useStateFilter,
  type AttributedStateFilter,
} from "@plugins/ads/frontend/features/ads-attributed-table";
export { AdsInspector } from "@plugins/ads/frontend/features/ads-inspector";

// Buzón de análisis con IA — la Page lo compone en un modal.
// Re-exportadas para tests (mismo patrón que el resto de features del plugin).
export { TriggerRun } from "@plugins/ads/frontend/features/trigger-run";
export { RunResult } from "@plugins/ads/frontend/features/run-result";
export { HitlDecision } from "@plugins/ads/frontend/features/hitl-decision";
