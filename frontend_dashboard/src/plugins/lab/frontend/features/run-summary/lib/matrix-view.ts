/**
 * Matriz episodios × checks del Resumen: la misma vista que Calidad LLM
 * (`ComplianceMatrixTable` de `@/shared/ui`), con las filas de un bot de la
 * corrida. La lógica vive en `@/shared/lib` (`quality-matrix.ts`, 2026-10-02).
 */

export {
  filterMatrixRows as filterRows,
  matrixFinalStages as finalStages,
  matrixGroups,
  matrixRowKey as rowKey,
  toMatrixRowView as toRowView,
  type MatrixVerdictFilter as VerdictFilter,
} from "@/shared/lib";
