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

import { usePluginHost, useSelection } from "@/shared/lib";
import { Icon } from "@/shared/ui";

import {
  useTrackedOrders,
  useTrackedOrdersEvents,
} from "@plugins/eta/frontend/entities/tracked-order";

import {
  EtaList,
  FILTER_LABELS as ETA_LABELS,
  useEtaFilters,
} from "@plugins/eta/frontend/features/eta-list";
import { EtaCards } from "@plugins/eta/frontend/features/eta-cards";
import { EtaChat } from "@plugins/eta/frontend/features/eta-chat";

export function EtaSection() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedTrackedId, setSelectedTrackedId] = useSelection("eta");
  useTrackedOrdersEvents(); // push del stream → invalidaciones (F1)
  // `isError` SE MUESTRA (no se traga): con el default `= []`, un fallo de
  // fetch o de validación Zod vaciaba el tablero en silencio y parecía "no hay
  // pedidos" (L-10 — un evento `cancelled` fuera del enum vació la sección).
  const { data: tracked = [], isError } = useTrackedOrders();
  const f = useEtaFilters(tracked);
  const selected = useMemo(
    () => tracked.find((o) => o.id === selectedTrackedId) ?? null,
    [tracked, selectedTrackedId],
  );

  if (isError) {
    return (
      <main className="eta-canvas">
        <div className="eta-empty">
          <span className="ee-ico"><Icon.alert /></span>
          <div className="ee-t">No se pudo cargar el seguimiento</div>
          <div className="ee-s">
            El backend no respondió o devolvió un formato inesperado.
            Reintentamos automáticamente cada 5 segundos — si persiste, revisa
            la consola del navegador y los logs del API.
          </div>
        </div>
      </main>
    );
  }

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
