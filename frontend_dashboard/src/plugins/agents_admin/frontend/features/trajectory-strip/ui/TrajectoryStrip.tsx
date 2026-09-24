import { useMemo } from "react";

import { TrajectoryStrip as TrajectoryStripChart } from "@/shared/ui";
import {
  buildStripModel,
  type CheckRegistry,
  type CheckResult,
  type Trajectory,
} from "@plugins/agents_admin/frontend/entities/scorecard";

interface Props {
  trajectory: Trajectory;
  results: readonly CheckResult[];
  /** Aporta nombres de checks y la alerta del carril de estado. */
  registry?: CheckRegistry;
  selectedCheckId: string | null;
  onSelectCheck: (checkId: string) => void;
}

/**
 * Tira de trayectoria de un episodio del scorecard: la entity arma el modelo
 * de vista (misma `Trayectoria` que evaluó el scorecard) y la tira compartida
 * de `@/shared/ui` lo dibuja.
 */
export function TrajectoryStrip({ trajectory, results, registry, selectedCheckId, onSelectCheck }: Props) {
  const model = useMemo(
    () => buildStripModel(trajectory, results, registry),
    [trajectory, results, registry],
  );
  return <TrajectoryStripChart model={model} selectedCheckId={selectedCheckId} onSelectCheck={onSelectCheck} />;
}

export default TrajectoryStrip;
