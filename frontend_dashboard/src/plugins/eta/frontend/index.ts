/**
 * Plugin `eta` — barrel del frontend.
 *
 * Exporta `default` (EtaSection) que el shell consume via lazy import desde
 * el registry. Plus named re-exports para tests y back-compat.
 */
export { default, EtaSection } from "./EtaSection";
export type { EtaSectionProps } from "./EtaSection";

export { EtaList, FILTER_LABELS, useEtaFilters } from "@plugins/eta/frontend/features/eta-list";
export { EtaCards } from "@plugins/eta/frontend/features/eta-cards";
export { EtaChat } from "@plugins/eta/frontend/features/eta-chat";
