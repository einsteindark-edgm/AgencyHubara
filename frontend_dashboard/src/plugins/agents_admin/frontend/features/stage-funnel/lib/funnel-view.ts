import type { FunnelRowView } from "@/shared/lib";
import { sortFunnel, type FunnelRow } from "@plugins/agents_admin/frontend/entities/check-stats";
import { stageColor, stageLabel } from "@plugins/agents_admin/frontend/entities/scorecard";

/**
 * Embudo del contrato → vista genérica de `@/shared/ui` (`StageFunnel`): en el
 * orden del guion de ventas y con rótulo y color de cada etapa, que son del
 * dominio del scorecard y no de la gráfica.
 */
export function toFunnelView(funnel: readonly FunnelRow[]): FunnelRowView[] {
  return sortFunnel(funnel).map((r) => ({
    ...r,
    label: stageLabel(r.stage),
    color: stageColor(r.stage),
  }));
}
