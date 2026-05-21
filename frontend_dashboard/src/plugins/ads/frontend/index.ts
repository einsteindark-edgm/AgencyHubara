/**
 * Plugin `ads` — barrel del frontend.
 *
 * Exporta `default` (AdsSection) que el shell consume via lazy import desde
 * el registry. Plus named re-exports de cada feature para tests y back-compat.
 */
export { default, AdsSection } from "./AdsSection";
export type { AdsSectionProps } from "./AdsSection";

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
