/**
 * Tab Audiencia del inspector — la audiencia REAL del envío (misma lógica
 * que usará el disparo; quiet hours se evalúa recién en ese momento):
 * destinatarios clickeables (abren el visor de conversación) + sección
 * atenuada de los que NO reciben, con razón legible.
 *
 * La query usa la campaña GUARDADA: `updatedAtMs` viaja en la key, así el
 * PUT de segmentos (que refresca la campaña) rehace el fetch sin acoplar
 * la mutación de campaign a la entity audience.
 */

import { useState } from "react";

import {
  skippedReasonLabel,
  useCampaignAudience,
} from "@plugins/marketing/frontend/entities/audience";
import type { Campaign } from "@plugins/marketing/frontend/entities/campaign";
import {
  AudienceViewer,
  RecipientRow,
} from "@plugins/marketing/frontend/features/audience-viewer";
import { apiErrorDetail, fmtN } from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
}

export function AudiencePanel({ campaign }: Props) {
  const { data, isPending, error } = useCampaignAudience(
    campaign.id,
    campaign.updatedAtMs,
  );
  // UI state local y colocado (regla #3): sesión abierta en el visor;
  // null = cerrado.
  const [sessionId, setSessionId] = useState<string | null>(null);

  if (isPending) {
    return (
      <div className="flex flex-col gap-1.5" data-testid="audience-skeleton">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-9 animate-pulse rounded-md bg-white/[0.05]" />
        ))}
      </div>
    );
  }

  if (error) {
    return <p className="text-[11.5px] text-danger">{apiErrorDetail(error)}</p>;
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] font-semibold tabular-nums text-fg">
        {fmtN(data.total)} destinatarios
      </p>

      {data.recipients.length === 0 ? (
        <p className="text-[11.5px] leading-relaxed text-fg-muted">
          Sin destinatarios todavía — Elegí segmentos en el paso 4.
        </p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {data.recipients.map((r) => (
            <li key={r.sessionId}>
              <RecipientRow
                recipient={r}
                onClick={() => setSessionId(r.sessionId)}
              />
            </li>
          ))}
        </ul>
      )}

      {data.skipped.length > 0 ? (
        <details className="opacity-70">
          <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
            No reciben ({fmtN(data.skipped.length)})
          </summary>
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {data.skipped.map((s) => (
              <li
                key={s.sessionId}
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5"
              >
                <span className="text-[11px] tabular-nums text-fg-faint">
                  {s.phone}
                </span>
                <span className="ml-auto text-right text-[10.5px] text-fg-faint">
                  {skippedReasonLabel(s.reason)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {sessionId !== null ? (
        <AudienceViewer
          campaign={campaign}
          recipients={data.recipients}
          removed={data.skipped.filter(
            (s) => s.reason === "quitado_por_operador",
          )}
          total={data.total}
          sessionId={sessionId}
          onSelectSession={setSessionId}
          onClose={() => setSessionId(null)}
        />
      ) : null}
    </div>
  );
}
