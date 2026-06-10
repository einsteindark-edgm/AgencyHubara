/**
 * `CatalogSyncSection` — la Page del plugin catalog.
 *
 * Orquesta 3 paneles cableados al sync REAL del catálogo (Medusa → copia local
 * → Meta):
 *   - izquierda  → `SyncHistory`   (historial real de runs)
 *   - centro     → `SyncRunner`    (botón Sincronizar + step-by-step en vivo)
 *   - derecha    → `SyncInspector` (estado de la copia local + run seleccionado)
 *
 * Mantiene la firma de props del shell (`selectedJobId`/`setSelectedJobId` es el
 * "item seleccionado" genérico de la sección — acá, el workflow_id del run).
 * El run activo = la selección explícita si es un workflow_id real, sino el más
 * reciente del historial (así el centro muestra algo útil al entrar).
 */

import { usePluginHost, useSelection } from "@/shared/lib";

import { useSyncHistory } from "@plugins/catalog/frontend/entities/catalog-sync";
import { SyncHistory } from "@plugins/catalog/frontend/features/sync-history";
import { SyncInspector } from "@plugins/catalog/frontend/features/sync-inspector";
import { SyncRunner } from "@plugins/catalog/frontend/features/sync-runner";

/** Los workflow_id reales arrancan con este prefijo (ver el backend). */
const WORKFLOW_ID_PREFIX = "catalog-sync";

export function CatalogSyncSection() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedJobIdRaw, setSelectedJobId] = useSelection("catalog", "");
  const selectedJobId = selectedJobIdRaw ?? "";
  const { data } = useSyncHistory();

  const selectedReal = selectedJobId.startsWith(WORKFLOW_ID_PREFIX)
    ? selectedJobId
    : null;
  const latest = data?.syncs[0]?.workflow_id ?? null;
  const activeId = selectedReal ?? latest;

  return (
    <>
      {showSidebar && (
        <SyncHistory selectedId={activeId} onSelect={setSelectedJobId} />
      )}
      <SyncRunner activeId={activeId} onSelect={setSelectedJobId} />
      {showInspector && <SyncInspector activeId={activeId} />}
    </>
  );
}

export default CatalogSyncSection;
