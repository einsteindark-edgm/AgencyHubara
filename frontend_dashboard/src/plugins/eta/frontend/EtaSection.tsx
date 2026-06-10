/**
 * `EtaSection` — la Page del plugin eta.
 *
 * Extraída de `pages/Dashboard.tsx` en PR6 (refactor a plugins). Mantiene la
 * firma de props que tenía cuando vivía inline en el shell.
 *
 * Plugin frontend-only — los datos (`tracked-order`) viven en `entities/`
 * (shared cross-plugin). El plugin no aporta backend ni worker.
 */
import { useMemo } from "react";

import { useTrackedOrders } from "@plugins/eta/frontend/entities/tracked-order";

import {
  EtaList,
  FILTER_LABELS as ETA_LABELS,
  useEtaFilters,
} from "@plugins/eta/frontend/features/eta-list";
import { EtaCards } from "@plugins/eta/frontend/features/eta-cards";
import { EtaChat } from "@plugins/eta/frontend/features/eta-chat";

export interface EtaSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedTrackedId: string | null;
  setSelectedTrackedId: (id: string) => void;
}

export function EtaSection({
  showSidebar,
  showInspector,
  selectedTrackedId,
  setSelectedTrackedId,
}: EtaSectionProps) {
  const { data: tracked = [] } = useTrackedOrders();
  const f = useEtaFilters(tracked);
  const selected = useMemo(
    () => tracked.find((o) => o.id === selectedTrackedId) ?? null,
    [tracked, selectedTrackedId],
  );

  return (
    <>
      {showSidebar && (
        <EtaList orders={tracked} filter={f.filter} setFilter={f.setFilter} />
      )}
      <EtaCards
        orders={f.list}
        filterLabel={ETA_LABELS[f.filter]}
        selectedId={selectedTrackedId}
        onSelect={setSelectedTrackedId}
      />
      {showInspector && <EtaChat order={selected} />}
    </>
  );
}

export default EtaSection;
