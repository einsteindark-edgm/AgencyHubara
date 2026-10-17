/**
 * Control del envío por lotes en curso (CampaignSendWorkflowV2): avance,
 * Pausar / Reanudar y Cancelar envío.
 *
 * Pausar y cancelar actúan entre lotes: el lote en vuelo termina (lo dice el
 * texto). Cancelar no se deshace — confirmación inline de dos pasos (regla
 * #6: cero window.confirm). Los errores (409 con el motivo del backend) se
 * derivan de las mutations.
 */

import { useState } from "react";

import {
  useCancelCampaign,
  usePauseCampaign,
  useResumeCampaign,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import {
  apiErrorDetail,
  fmtDateTimeMs,
  fmtN,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
}

export function SendControls({ campaign }: Props) {
  const pause = usePauseCampaign(campaign.id);
  const resume = useResumeCampaign(campaign.id);
  const cancel = useCancelCampaign(campaign.id);
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  const paused = campaign.status === "paused";
  const progress = campaign.progress ?? null;
  const cancelling = progress?.state === "cancelling";
  const error = pause.error ?? resume.error ?? cancel.error;
  const busy = pause.isPending || resume.isPending || cancel.isPending;

  if (progress === null) {
    // El workflow viejo no reporta avance ni acepta pausa/cancelación.
    return (
      <div className="shrink-0 border-b border-line bg-sidebar/40 px-5 py-2 text-[11.5px] text-fg-muted">
        Este envío arrancó con la versión anterior: no se puede pausar ni cancelar; termina solo.
      </div>
    );
  }

  return (
    <div className="flex shrink-0 flex-col gap-1.5 border-b border-line bg-sidebar/40 px-5 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-[11.5px] text-fg-muted">
          {progress.total > 0 ? (
            <>
              <span className="font-semibold tabular-nums text-fg">
                {fmtN(progress.processed)} de {fmtN(progress.total)}
              </span>{" "}
              procesados · {fmtN(progress.sent)} enviados · {fmtN(progress.failed)} fallidos ·{" "}
              {fmtN(progress.optedOut)} bajas · {fmtN(progress.skipped)} omitidos
            </>
          ) : (
            "Preparando el envío…"
          )}
          {cancelling
            ? " — cancelando: el lote en curso termina y no sale otro."
            : paused
              ? " — pausada: no sale otro lote hasta reanudar."
              : null}
        </p>
        {cancelling ? null : paused ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => resume.mutate()}
            className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Reanudar envío
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => pause.mutate()}
            className="rounded-md border border-warn/40 px-3 py-1.5 text-[12px] font-semibold text-warn hover:bg-warn-soft disabled:opacity-50"
          >
            Pausar envío
          </button>
        )}
        {cancelling ? null : confirmingCancel ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirmingCancel(false);
                cancel.mutate();
              }}
              className="rounded-md bg-danger px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Sí, cancelar el envío
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(false)}
              className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05]"
            >
              No
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirmingCancel(true)}
            className="rounded-md border border-danger/40 px-3 py-1.5 text-[12px] font-semibold text-danger hover:bg-danger-soft disabled:opacity-50"
          >
            Cancelar envío
          </button>
        )}
      </div>
      {paused && progress.pauseReason ? (
        <p role="status" className="text-[11px] text-warn">
          Se pausó sola: {progress.pauseReason}
          {progress.resumeAtMs !== null
            ? ` Sigue solo el ${fmtDateTimeMs(progress.resumeAtMs)}.`
            : ""}
        </p>
      ) : null}
      {confirmingCancel ? (
        <p className="text-[11px] text-danger">
          No se deshace: el lote en curso termina, no sale ningún otro y la campaña queda cancelada.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] text-danger">
          {apiErrorDetail(error)}
        </p>
      ) : null}
    </div>
  );
}
