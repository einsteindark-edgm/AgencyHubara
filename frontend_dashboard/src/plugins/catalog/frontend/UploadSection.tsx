/**
 * `UploadSection` — la Page del plugin catalog.
 *
 * Extraída de `pages/Dashboard.tsx` en PR5 (refactor a plugins). Mantiene la
 * firma de props que tenía cuando vivía inline en el shell.
 *
 * El plugin frontend complementa el worker Temporal `catalog.workers.sync`:
 * la UI muestra jobs históricos, permite disparar sync manuales y inspeccionar
 * detalles. La interacción runtime con el worker se hace via REST endpoints
 * registrados por el plugin chats (`/api/dashboard/*`) — el plugin catalog no
 * aporta API HTTP propio por ahora.
 */
import { UploadJobs } from "@plugins/catalog/frontend/features/upload-jobs";
import { UploadWizard } from "@plugins/catalog/frontend/features/upload-wizard";
import { UploadInspector } from "@plugins/catalog/frontend/features/upload-inspector";

export interface UploadSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedJobId: string;
  setSelectedJobId: (id: string) => void;
}

export function UploadSection({
  showSidebar,
  showInspector,
  selectedJobId,
  setSelectedJobId,
}: UploadSectionProps) {
  return (
    <>
      {showSidebar && (
        <UploadJobs
          selectedJobId={selectedJobId}
          onSelect={setSelectedJobId}
        />
      )}
      <UploadWizard />
      {showInspector && <UploadInspector />}
    </>
  );
}

export default UploadSection;
