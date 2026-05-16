/**
 * Plugin `catalog` — barrel del frontend.
 *
 * Exporta `default` (UploadSection) que el shell consume via
 * `lazy(() => import("@plugins/catalog/frontend"))` desde el registry generado.
 * Plus named re-exports de las features para tests y back-compat.
 */
export { default, UploadSection } from "./UploadSection";
export type { UploadSectionProps } from "./UploadSection";

export { UploadJobs } from "@plugins/catalog/frontend/features/upload-jobs";
export { UploadWizard } from "@plugins/catalog/frontend/features/upload-wizard";
export { UploadInspector } from "@plugins/catalog/frontend/features/upload-inspector";
