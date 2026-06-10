/**
 * Panel izquierdo: historial REAL de sincronizaciones (Temporal visibility via
 * `GET /api/catalog/syncs`). "En curso" = runs RUNNING; "Recientes" =
 * completados/fallidos. Click selecciona el run para que el centro + inspector
 * muestren su detalle.
 */

import {
  formatRelativeTime,
  syncStatusLabel,
  useSyncHistory,
  type SyncHistoryItem,
} from "@plugins/catalog/frontend/entities/catalog-sync";
import { Icon } from "@/shared/ui";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** "dashboard" → "Desde el dashboard"; "manual" → "Manual (CLI)"; resto crudo. */
function humanizeTrigger(triggeredBy: string): string {
  if (triggeredBy === "dashboard") return "Desde el dashboard";
  if (triggeredBy === "manual") return "Manual (CLI)";
  return triggeredBy.replace(/_/g, " ");
}

export function SyncHistory({ selectedId, onSelect }: Props) {
  const { data, isLoading } = useSyncHistory();
  const syncs = data?.syncs ?? [];
  const available = data?.available ?? true;
  const running = syncs.filter((s) => s.status === "running");
  const recent = syncs.filter((s) => s.status !== "running");

  return (
    <aside className="sidebar">
      <div className="sb-head">
        <h2>Sincronizaciones</h2>
      </div>

      <div className="sb-section">
        <div className="sb-section-h">
          <span>En curso</span>
        </div>
        {running.length === 0 && <EmptyLine text="Ninguna corriendo" />}
        {running.map((s) => (
          <SyncRow
            key={s.workflow_id}
            sync={s}
            selected={selectedId === s.workflow_id}
            onSelect={onSelect}
          />
        ))}
      </div>

      <div className="sb-section">
        <div className="sb-section-h">
          <span>Recientes</span>
        </div>
        {!available && (
          <EmptyLine text="Historial no disponible — Temporal no responde." />
        )}
        {available && recent.length === 0 && !isLoading && (
          <EmptyLine text="Sin sincronizaciones todavía." />
        )}
        {recent.map((s) => (
          <SyncRow
            key={s.workflow_id}
            sync={s}
            selected={selectedId === s.workflow_id}
            onSelect={onSelect}
          />
        ))}
      </div>

      <div className="sb-foot">
        <span>
          {syncs.length} {syncs.length === 1 ? "sincronización" : "sincronizaciones"}
        </span>
      </div>
    </aside>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)" }}>
      {text}
    </div>
  );
}

interface RowProps {
  sync: SyncHistoryItem;
  selected: boolean;
  onSelect: (id: string) => void;
}

function SyncRow({ sync, selected, onSelect }: RowProps) {
  const tone =
    sync.status === "running"
      ? "live"
      : sync.status === "completed"
        ? "ok"
        : "err";
  const ico =
    sync.status === "running" ? (
      <Icon.refresh />
    ) : sync.status === "completed" ? (
      <Icon.check />
    ) : (
      <Icon.alert />
    );
  const badgeClass =
    sync.status === "running" ? "med" : sync.status === "completed" ? "high" : "low";

  return (
    <div
      className={"job-row" + (selected ? " sel" : "")}
      onClick={() => onSelect(sync.workflow_id)}
    >
      <div className={"jico " + tone}>{ico}</div>
      <div className="jb">
        <div className="jn">{humanizeTrigger(sync.triggered_by)}</div>
        <div className="jm">
          <span className="jt">{formatRelativeTime(sync.started_at_ms)}</span>
        </div>
      </div>
      <span className={"conf " + badgeClass}>{syncStatusLabel(sync.status)}</span>
    </div>
  );
}
