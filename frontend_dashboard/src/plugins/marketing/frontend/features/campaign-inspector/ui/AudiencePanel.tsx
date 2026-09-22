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
  SKIP_DADO_DE_BAJA,
  optOutSourceLabel,
  skippedReasonLabel,
  useCampaignAudience,
  type SkippedContact,
} from "@plugins/marketing/frontend/entities/audience";
import type { Campaign } from "@plugins/marketing/frontend/entities/campaign";
import {
  AudienceViewer,
  RecipientRow,
} from "@plugins/marketing/frontend/features/audience-viewer";
import {
  apiErrorDetail,
  fmtDateMs,
  fmtN,
} from "@plugins/marketing/frontend/lib/format";

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

  // Las bajas van aparte de "No reciben": no es un filtro de esta campaña,
  // es un contacto al que ya NO se le puede enviar ninguna.
  const optedOut = data.skipped.filter((s) => s.reason === SKIP_DADO_DE_BAJA);
  const notReceiving = data.skipped.filter((s) => s.reason !== SKIP_DADO_DE_BAJA);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] font-semibold tabular-nums text-fg">
        {fmtN(data.total)} destinatarios
      </p>

      {data.recipients.length === 0 ? (
        <p className="text-[11.5px] leading-relaxed text-fg-muted">
          Sin destinatarios todavía — Elegí segmentos o importá un CSV en el paso 4.
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

      {optedOut.length > 0 ? (
        <section className="rounded-lg border border-danger/30 bg-danger/[0.06] p-2.5">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-danger">
            Bajas ({fmtN(optedOut.length)})
          </h3>
          <p className="mt-0.5 text-[11px] leading-snug text-fg-muted">
            Pidieron no recibir más promociones: ya no se les puede enviar
            ninguna campaña.
          </p>
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {optedOut.map((s) => (
              <li key={s.sessionId} className="rounded-md px-2.5 py-1.5">
                <span className="text-[11px] tabular-nums text-fg">{s.phone}</span>
                <OptOutDetail contact={s} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {notReceiving.length > 0 ? (
        <details className="opacity-70">
          <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
            No reciben ({fmtN(notReceiving.length)})
          </summary>
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {notReceiving.map((s) => (
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

/** "se dio de baja desde WhatsApp · por la campaña Promo madre · 22 sept 2025".
 *  Cada pieza solo si existe: una baja vieja (sin detalle) muestra solo el
 *  teléfono, sin inventar campaña ni fecha. */
function OptOutDetail({ contact }: { contact: SkippedContact }) {
  const parts: string[] = [];
  const via = optOutSourceLabel(contact.optedOutSource);
  if (via) parts.push(via);
  if (contact.optedOutCampaignName) {
    parts.push(`por la campaña ${contact.optedOutCampaignName}`);
  } else if (contact.optedOutCampaignId) {
    parts.push(`por la campaña ${contact.optedOutCampaignId}`);
  }
  if (contact.optedOutAtMs !== null && contact.optedOutAtMs !== undefined) {
    parts.push(fmtDateMs(contact.optedOutAtMs));
  }
  if (parts.length === 0) return null;
  return (
    <p className="text-[10.5px] leading-snug text-fg-faint">{parts.join(" · ")}</p>
  );
}
