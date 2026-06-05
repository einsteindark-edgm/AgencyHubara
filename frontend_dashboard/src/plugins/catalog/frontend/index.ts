/**
 * Plugin `catalog` — barrel del frontend.
 *
 * Exporta `default` (CatalogSyncSection) que el shell consume via
 * `lazy(() => import("@plugins/catalog/frontend"))` desde el registry generado.
 * Plus named re-exports de las features para tests y back-compat.
 */
export { default, CatalogSyncSection } from "./CatalogSyncSection";
export type { CatalogSyncSectionProps } from "./CatalogSyncSection";

export { SyncHistory } from "@plugins/catalog/frontend/features/sync-history";
export { SyncRunner } from "@plugins/catalog/frontend/features/sync-runner";
export { SyncInspector } from "@plugins/catalog/frontend/features/sync-inspector";
