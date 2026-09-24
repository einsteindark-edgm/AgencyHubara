/**
 * Barrel del kit UI compartido. Sólo se exportan primitivas con uso cross-feature.
 * Si algo termina siendo usado por una sola feature, vive dentro de esa feature
 * (regla §11 del manifiesto FSD).
 */

export { Icon, type IconName } from "./Icon";
export { MacButton } from "./Button";
export { Panel, InsBlock } from "./Panel";
export { Avatar } from "./Avatar";
export { MissingData } from "./MissingData";
export { ErrorBoundary } from "./ErrorBoundary";
export { Toolbar, StatusBar, type SectionKey } from "./chrome";
export { Markdown } from "./Markdown";
export { DateRangeFilter } from "./DateRangeFilter";
export { Modal } from "./Modal";
export { SequenceTrace } from "./SequenceTrace";
export { TraceStepDetail } from "./TraceStepDetail";
// Gráficas de calidad de un bot (Calidad LLM de agents_admin + laboratorio).
// Reciben formas de vista genéricas de `@/shared/lib` (`quality-view`, `trajectory-strip`).
export { FailurePareto } from "./quality/FailurePareto";
export { StageFunnel } from "./quality/StageFunnel";
export { CheckTrend } from "./quality/CheckTrend";
export { VerdictTiles } from "./quality/VerdictTiles";
export { TrajectoryStrip } from "./quality/TrajectoryStrip";
export { ComplianceMatrixTable, ComplianceMatrixLegend } from "./quality/ComplianceMatrixTable";
