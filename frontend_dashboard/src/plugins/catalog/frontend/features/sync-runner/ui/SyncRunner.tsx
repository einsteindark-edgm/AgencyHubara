/**
 * Panel central: el botón **Sincronizar** + el step-by-step EN VIVO de los 3
 * pasos reales del CatalogSyncWorkflow (Medusa → copia local → Meta). No simula
 * nada: dispara el workflow real (`useTriggerSync`) y pollea su estado real
 * (`useSyncStatus`).
 */

import { useState } from "react";
import {
  formatRelativeTime,
  useSnapshotInfo,
  useSyncStatus,
  useTriggerSync,
  type SyncStep,
  type SyncStepStatus,
} from "@/entities/catalog-sync";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  /** Run a mostrar (selección del historial o el más reciente). */
  activeId: string | null;
  /** Al disparar un sync, selecciona el run nuevo para los 3 paneles. */
  onSelect: (id: string) => void;
}

// Pasos por defecto (placeholder antes de que exista un run con su query).
const DEFAULT_STEPS: { key: string; label: string }[] = [
  { key: "pull", label: "Traer productos de Medusa" },
  { key: "write", label: "Escribir copia local (snapshot)" },
  { key: "push", label: "Propagar a Meta Commerce Catalog" },
];

const STATUS_TEXT: Record<SyncStepStatus, string> = {
  pending: "Pendiente",
  running: "En curso",
  done: "Listo",
  skipped: "Omitido",
  failed: "Falló",
};

export function SyncRunner({ activeId, onSelect }: Props) {
  const snapshot = useSnapshotInfo();
  const status = useSyncStatus(activeId);
  const trigger = useTriggerSync();
  const [error, setError] = useState<string | null>(null);

  const detail = status.data;
  const isRunning = detail?.status === "running";
  const busy = trigger.isPending || isRunning;

  const onSync = () => {
    setError(null);
    trigger.mutate(undefined, {
      onSuccess: (res) => onSelect(res.workflow_id),
      onError: (e) => setError(e.message),
    });
  };

  const steps: SyncStep[] =
    detail?.steps && detail.steps.length > 0
      ? detail.steps
      : DEFAULT_STEPS.map((s) => ({
          key: s.key,
          label: s.label,
          status: "pending" as SyncStepStatus,
          detail: "",
        }));

  const snap = snapshot.data;

  return (
    <main className="up-canvas">
      <div className="up-head">
        <div className="ttl">
          <div>
            <h1>Catálogo de productos</h1>
            <div className="sub">
              Copia local que usa el agente de Sales para vender. Sincronizá para
              traer los últimos productos de Medusa.
            </div>
          </div>
          <div className="actions">
            <MacButton primary onClick={onSync} disabled={busy}>
              <Icon.refresh /> {busy ? "Sincronizando…" : "Sincronizar"}
            </MacButton>
          </div>
        </div>
      </div>

      <div className="up-body">
        {/* Estado de la copia local actual */}
        <div style={cardStyle}>
          <div style={cardHeadStyle}>
            <h3 style={{ margin: 0, fontSize: 13 }}>Copia local actual</h3>
            {snap?.exists && (
              <span
                className="conf"
                style={{
                  background: snap.stale
                    ? "rgba(255,159,10,0.15)"
                    : "rgba(48,209,88,0.15)",
                  color: snap.stale ? "#ffb44a" : "#5be07b",
                }}
              >
                {snap.stale ? "Desactualizada" : "Fresca"}
              </span>
            )}
          </div>
          {snap?.exists ? (
            <div style={{ display: "flex", gap: 24, marginTop: 10 }}>
              <Metric value={String(snap.product_count)} label="productos" />
              <Metric
                value={
                  snap.fetched_at
                    ? formatRelativeTime(new Date(snap.fetched_at).getTime())
                    : "—"
                }
                label="última sync"
              />
              <Metric value={snap.version ? snap.version.slice(0, 8) : "—"} label="versión" mono />
            </div>
          ) : (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--fg-mute)" }}>
              Todavía no hay copia local. Sincronizá para crearla — es lo que el
              agente de Sales lee para vender.
            </div>
          )}
        </div>

        {/* Error del disparo */}
        {error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12,
              color: "#ff7269",
              padding: "9px 12px",
              background: "rgba(255,69,58,0.08)",
              border: "0.5px solid rgba(255,69,58,0.25)",
              borderRadius: 8,
            }}
          >
            <Icon.alert /> No pude disparar el sync: {error}
          </div>
        )}

        {/* Step-by-step en vivo */}
        <div style={cardStyle}>
          <div style={cardHeadStyle}>
            <h3 style={{ margin: 0, fontSize: 13 }}>Progreso de la sincronización</h3>
            {isRunning && <span className="live">EN VIVO</span>}
          </div>
          <div style={{ marginTop: 6 }}>
            {steps.map((step) => (
              <StepRow key={step.key} step={step} />
            ))}
          </div>
          {!activeId && (
            <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--fg-mute)" }}>
              Apretá <b>Sincronizar</b> para ejecutar la descarga real desde Medusa.
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--surface-2, rgba(255,255,255,0.03))",
  border: "0.5px solid rgba(255,255,255,0.08)",
  borderRadius: 10,
  padding: "14px 16px",
};

const cardHeadStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

function Metric({
  value,
  label,
  mono,
}: {
  value: string;
  label: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 20,
          fontWeight: 700,
          color: "var(--fg)",
          fontFamily: mono ? "var(--font-mono)" : undefined,
        }}
      >
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>{label}</div>
    </div>
  );
}

function StepRow({ step }: { step: SyncStep }) {
  const dimmed = step.status === "pending";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "11px 2px",
        borderBottom: "0.5px solid rgba(255,255,255,0.06)",
      }}
    >
      <StepIndicator status={step.status} />
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: dimmed ? "var(--fg-mute)" : "var(--fg)",
          }}
        >
          {step.label}
        </div>
        {step.detail && (
          <div style={{ fontSize: 11.5, color: "var(--fg-mute)", marginTop: 2 }}>
            {step.detail}
          </div>
        )}
      </div>
      <span
        style={{
          fontSize: 11,
          color: stepToneColor(step.status),
          fontWeight: 500,
        }}
      >
        {STATUS_TEXT[step.status]}
      </span>
    </div>
  );
}

function stepToneColor(status: SyncStepStatus): string {
  switch (status) {
    case "done":
      return "#5be07b";
    case "failed":
      return "#ff7269";
    case "running":
      return "var(--accent-fg, #0a84ff)";
    default:
      return "var(--fg-mute)";
  }
}

function StepIndicator({ status }: { status: SyncStepStatus }) {
  if (status === "done") {
    return (
      <span style={{ color: "#5be07b", display: "inline-flex" }}>
        <Icon.check />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span style={{ color: "#ff7269", display: "inline-flex" }}>
        <Icon.alert />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span
        style={{
          width: 13,
          height: 13,
          borderRadius: "50%",
          background: "var(--accent-fg, #0a84ff)",
          animation: "pulse 1.4s infinite",
          flexShrink: 0,
        }}
      />
    );
  }
  // pending / skipped → anillo hueco (skipped algo más tenue).
  return (
    <span
      style={{
        width: 13,
        height: 13,
        borderRadius: "50%",
        border:
          status === "skipped"
            ? "1.5px solid var(--fg-mute)"
            : "1.5px solid rgba(255,255,255,0.18)",
        flexShrink: 0,
      }}
    />
  );
}
