/**
 * Panel derecho: detalle REAL — el estado de la copia local (la que consume
 * Sales) + la metadata del run seleccionado.
 */

import {
  formatRelativeTime,
  syncStatusLabel,
  useSnapshotInfo,
  useSyncStatus,
} from "@plugins/catalog/frontend/entities/catalog-sync";
import { Icon, InsBlock } from "@/shared/ui";

interface Props {
  activeId: string | null;
}

export function SyncInspector({ activeId }: Props) {
  const snapshot = useSnapshotInfo();
  const status = useSyncStatus(activeId);
  const snap = snapshot.data;
  const detail = status.data;

  return (
    <aside className="inspector">
      <div className="insp-tabs">
        <button className="insp-tab on" title="Información">
          <Icon.info />
        </button>
      </div>

      <div className="insp-body">
        <InsBlock title="Copia local (la que usa Sales)">
          {snap?.exists ? (
            <>
              <Row label="Productos" value={String(snap.product_count)} />
              <Row
                label="Versión"
                value={snap.version ? snap.version.slice(0, 8) : "—"}
                mono
              />
              <Row
                label="Actualizada"
                value={
                  snap.fetched_at
                    ? formatRelativeTime(new Date(snap.fetched_at).getTime())
                    : "—"
                }
              />
              <Row
                label="Estado"
                value={snap.stale ? "Desactualizada" : "Fresca"}
                tone={snap.stale ? "err" : "ok"}
              />
            </>
          ) : (
            <div style={emptyStyle}>
              Todavía no hay copia local. Sincronizá para crearla.
            </div>
          )}
        </InsBlock>

        <InsBlock title="Sincronización seleccionada">
          {detail ? (
            <>
              <Row
                label="Estado"
                value={syncStatusLabel(detail.status)}
                tone={
                  detail.status === "completed"
                    ? "ok"
                    : detail.status === "failed" || detail.status === "cancelled"
                      ? "err"
                      : undefined
                }
              />
              <Row label="Productos" value={String(detail.product_count)} />
              <Row label="Iniciada" value={formatRelativeTime(detail.started_at_ms)} />
              {detail.finished_at_ms !== null && (
                <Row
                  label="Terminada"
                  value={formatRelativeTime(detail.finished_at_ms)}
                />
              )}
              {detail.error && (
                <div
                  style={{
                    marginTop: 8,
                    fontSize: 11.5,
                    color: "var(--color-danger)",
                    lineHeight: 1.4,
                  }}
                >
                  {detail.error}
                </div>
              )}
            </>
          ) : (
            <div style={emptyStyle}>
              Seleccioná una sincronización del historial para ver el detalle.
            </div>
          )}
        </InsBlock>
      </div>
    </aside>
  );
}

const emptyStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "var(--fg-mute)",
  lineHeight: 1.4,
};

function Row({
  label,
  value,
  tone,
  mono,
}: {
  label: string;
  value: string;
  tone?: "ok" | "err";
  mono?: boolean;
}) {
  const color =
    tone === "ok" ? "var(--color-ok)" : tone === "err" ? "var(--color-danger)" : "var(--fg)";
  return (
    <div className="form-row">
      <span className="lbl">{label}</span>
      <span
        className="val"
        style={{
          color,
          fontFamily: mono ? "var(--font-mono)" : undefined,
        }}
      >
        {value}
      </span>
    </div>
  );
}
