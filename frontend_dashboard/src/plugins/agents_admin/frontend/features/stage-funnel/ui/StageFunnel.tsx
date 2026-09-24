import { useMemo } from "react";

import { StageFunnel as StageFunnelChart } from "@/shared/ui";
import type { FunnelRow } from "@plugins/agents_admin/frontend/entities/check-stats";

import { toFunnelView } from "../lib/funnel-view";

interface Props {
  funnel: readonly FunnelRow[];
}

/**
 * Embudo de etapa terminal del scorecard: ordena por el guion y rotula las
 * etapas (dominio) y delega el dibujo a la gráfica compartida de `@/shared/ui`.
 */
export function StageFunnel({ funnel }: Props) {
  const rows = useMemo(() => toFunnelView(funnel), [funnel]);
  return <StageFunnelChart funnel={rows} />;
}

export default StageFunnel;
