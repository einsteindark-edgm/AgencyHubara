/**
 * `HitlDecision` — panel de aprobación humana (HITL). Se renderiza SOLO cuando
 * el run está en `awaiting_approval`: muestra el contexto que el agente dejó
 * para revisar (`awaiting`) y dos acciones (Aprobar / Rechazar) que resumen el
 * run vía `POST .../approve`.
 *
 * Consume la entity (`useApproveRun`). El `by` lo dejamos fijo en "dashboard"
 * (el backend no tiene aún identidad de operador acá — el access-token de
 * Cognito viaja en el header, pero el record solo guarda el string `by`).
 */
import { useApproveRun } from "@plugins/ads/frontend/entities/ad-analysis-run";

interface Props {
  runId: string;
  awaiting: unknown;
}

export function HitlDecision({ runId, awaiting }: Props) {
  const approve = useApproveRun(runId);

  const decide = (approved: boolean) => {
    if (approve.isPending) return;
    approve.mutate({ approved, by: "dashboard" });
  };

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-warn/40 bg-warn-soft p-3 text-fg">
      <div className="text-xs font-semibold uppercase tracking-wide text-warn">
        Esperando aprobación
      </div>
      <div className="flex flex-col gap-1">
        <div className="text-xs text-fg-muted">Contexto a revisar</div>
        <pre className="max-h-48 overflow-auto rounded-md border border-line bg-canvas p-2 font-mono text-xs text-fg-soft">
          {formatJson(awaiting)}
        </pre>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded-md bg-ok px-3 py-1.5 text-sm font-semibold text-win-bg hover:opacity-90 disabled:opacity-40"
          onClick={() => decide(true)}
          disabled={approve.isPending}
        >
          {approve.isPending ? "Enviando…" : "Aprobar"}
        </button>
        <button
          type="button"
          className="rounded-md border border-danger/50 px-3 py-1.5 text-sm font-semibold text-danger hover:bg-danger-soft disabled:opacity-40"
          onClick={() => decide(false)}
          disabled={approve.isPending}
        >
          Rechazar
        </button>
      </div>
      {approve.isError && (
        <div className="text-xs text-danger">
          No se pudo enviar la decisión. Reintentá.
        </div>
      )}
    </section>
  );
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}
