import { useState } from "react";

import {
  calibrationStatusLabel,
  formatKappa,
  formatRate,
  queueReasonLabel,
  useCreateLabel,
  useJudgeCalibration,
  useLabelQueue,
  type CalibrationStatus,
  type HumanVerdict,
  type LabelQueueItem,
} from "@plugins/agents_admin/frontend/entities/eval-label";
import {
  checkStatus,
  episodeLabel,
  levelLabel,
  statusColor,
  statusGlyph,
  statusLabel,
} from "@plugins/agents_admin/frontend/entities/scorecard";
import { Icon } from "@/shared/ui";

interface Props {
  /** Ventana (días) de la cola de etiquetado. */
  days?: number;
  limit?: number;
}

const STATUS_PILL: Record<CalibrationStatus, string> = {
  confiable: "bg-ok-soft text-green",
  revisar: "bg-warn-soft text-orange",
  sin_datos: "bg-neutral-soft text-fg-muted",
};

function itemKey(i: LabelQueueItem): string {
  return `${i.session_id}::${i.episode_id}::${i.check_id}`;
}

function QueueItem({
  item,
  pending,
  onLabel,
}: {
  item: LabelQueueItem;
  pending: boolean;
  onLabel: (verdict: HumanVerdict, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const judgeStatus = checkStatus(item.judge_verdict, "mayor");
  const noteId = `queue-note-${itemKey(item)}`;
  return (
    <li className="rounded-lg border border-line p-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-fg-muted">{item.check_id}</span>
        <span className="font-medium text-fg">{item.check_name || item.check_id}</span>
        <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-fg-muted">
          {queueReasonLabel(item.reason)}
        </span>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-fg-muted" title="Veredicto del juez">
          juez:
          <span
            className="inline-grid h-3.5 w-3.5 place-items-center rounded-full text-[9px] font-bold text-win-bg"
            style={{ background: statusColor(judgeStatus) }}
            aria-hidden="true"
          >
            {statusGlyph(judgeStatus)}
          </span>
          {item.judge_verdict === "falla" ? "falla" : statusLabel(judgeStatus)}
        </span>
      </div>
      <div className="mt-0.5 font-mono text-[11px] text-fg-faint" title={`${item.session_id} · ${item.episode_id}`}>
        {episodeLabel(item)}
      </div>
      {item.evidence && (
        <blockquote className="mt-1.5 rounded-r-md border-l-2 border-accent bg-white/5 px-2 py-1 text-xs text-fg-soft">
          {item.evidence}
        </blockquote>
      )}
      {item.critique && <p className="mt-1 text-xs text-fg-muted">Crítica del juez: {item.critique}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label htmlFor={noteId} className="sr-only">
          Nota para {item.check_id}
        </label>
        <input
          id={noteId}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Nota (opcional)"
          className="min-w-0 flex-1 rounded-md border border-line bg-white/5 px-2 py-1 text-xs text-fg placeholder:text-fg-faint"
        />
        <button
          type="button"
          disabled={pending}
          onClick={() => onLabel("pasa", note.trim())}
          className="rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-green hover:bg-white/5 disabled:opacity-50"
        >
          pasa
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => onLabel("falla", note.trim())}
          className="rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-red hover:bg-white/5 disabled:opacity-50"
        >
          falla
        </button>
      </div>
    </li>
  );
}

/**
 * Calibración del juez (Capa 3): por cada check de juez, cuánto coincide con
 * las etiquetas humanas (matriz TP/FP/TN/FN, TPR, TNR, kappa) y si ya es
 * confiable; debajo, la cola de veredictos del juez para etiquetar a mano.
 */
export function JudgeCalibration({ days = 30, limit = 20 }: Props) {
  const calibration = useJudgeCalibration();
  const queue = useLabelQueue(days, limit);
  const create = useCreateLabel();

  const pendingKey =
    create.isPending && create.variables
      ? `${create.variables.session_id}::${create.variables.episode_id}::${create.variables.check_id}`
      : null;

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2" aria-labelledby="calib-title">
        <div className="flex items-center gap-2">
          <Icon.shield />
          <h3 id="calib-title" className="text-sm font-semibold text-fg">
            ¿El juez es confiable?
          </h3>
        </div>
        {calibration.isLoading ? (
          <p className="text-sm text-fg-muted">Cargando calibración…</p>
        ) : calibration.isError ? (
          <p className="text-sm text-red" role="alert">
            No se pudo cargar la calibración del juez.
          </p>
        ) : (calibration.data?.checks.length ?? 0) === 0 ? (
          <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
            Aún no hay checks de juez con etiquetas. Etiqueta la cola de abajo para empezar a medirlo.
          </p>
        ) : (
          <>
            <p className="text-[11px] text-fg-faint">
              Confiable = al menos {calibration.data!.min_labels} etiquetas y κ ≥ {calibration.data!.kappa_threshold}.
              TPR: fallas reales que el juez detecta · TNR: pasadas reales que el juez respeta.
            </p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <table aria-label="Calibración del juez por check" className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-fg-faint">
                    <th scope="col" className="border-b border-line px-2 py-1.5">Check</th>
                    <th scope="col" className="border-b border-line px-2 py-1.5">Nivel</th>
                    {["n", "TP", "FP", "TN", "FN", "TPR", "TNR", "κ"].map((h) => (
                      <th key={h} scope="col" className="border-b border-line px-2 py-1.5 text-right">
                        {h}
                      </th>
                    ))}
                    <th scope="col" className="border-b border-line px-2 py-1.5">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {calibration.data!.checks.map((c) => (
                    <tr key={c.check_id} className="tabular-nums">
                      <th scope="row" className="border-b border-line px-2 py-1.5 text-left font-normal">
                        <span className="font-mono text-[11px] text-fg-muted">{c.check_id}</span>{" "}
                        <span className="text-fg">{c.name}</span>
                      </th>
                      <td className="border-b border-line px-2 py-1.5 text-fg-muted">{levelLabel(c.level)}</td>
                      {[c.n, c.tp, c.fp, c.tn, c.fn].map((v, i) => (
                        <td key={i} className="border-b border-line px-2 py-1.5 text-right text-fg">
                          {v}
                        </td>
                      ))}
                      <td className="border-b border-line px-2 py-1.5 text-right text-fg">{formatRate(c.tpr)}</td>
                      <td className="border-b border-line px-2 py-1.5 text-right text-fg">{formatRate(c.tnr)}</td>
                      <td className="border-b border-line px-2 py-1.5 text-right text-fg">{formatKappa(c.kappa)}</td>
                      <td className="border-b border-line px-2 py-1.5">
                        <span className={"whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium " + STATUS_PILL[c.status]}>
                          {calibrationStatusLabel(c.status)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="flex flex-col gap-2" aria-labelledby="queue-title">
        <div className="flex items-center gap-2">
          <Icon.tag />
          <h3 id="queue-title" className="text-sm font-semibold text-fg">
            Cola de etiquetado
          </h3>
          <span className="text-[11px] text-fg-faint">últimos {days} días</span>
        </div>
        {create.isError && (
          <p className="text-xs text-red" role="alert">
            No se pudo guardar la etiqueta: {create.error.message}
          </p>
        )}
        {queue.isLoading ? (
          <p className="text-sm text-fg-muted">Cargando cola…</p>
        ) : queue.isError ? (
          <p className="text-sm text-red" role="alert">
            No se pudo cargar la cola de etiquetado.
          </p>
        ) : (queue.data?.items.length ?? 0) === 0 ? (
          <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
            No hay veredictos del juez pendientes de etiquetar.
          </p>
        ) : (
          <ul className="flex flex-col gap-2" aria-label="Veredictos del juez por etiquetar">
            {queue.data!.items.map((item) => (
              <QueueItem
                key={itemKey(item)}
                item={item}
                pending={pendingKey === itemKey(item)}
                onLabel={(verdict, note) =>
                  create.mutate({
                    session_id: item.session_id,
                    episode_id: item.episode_id,
                    check_id: item.check_id,
                    verdict,
                    note,
                  })
                }
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default JudgeCalibration;
