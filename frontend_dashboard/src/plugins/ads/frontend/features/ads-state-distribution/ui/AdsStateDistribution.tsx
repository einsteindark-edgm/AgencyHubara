/**
 * Barra apilada + leyenda de la distribución de estados conversacionales
 * de una campaña. Cada estado además muestra su valor estimado en pipeline
 * (ponderado por probabilidad de cierre) o ingresos reales (estado "ganado").
 *
 * Requiere `conversations` counts (clasificador conversacional pendiente) y
 * `avgTicket` (orders pendiente). Si faltan, mostramos un empty card con
 * el `dataPending` marker.
 */

import {
  ADS_STATES,
  ADS_STATE_ORDER,
  totalConversations,
  type AdsCampaign,
  type AdsState,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import { fmtMoney, fmtMoneyK, fmtN } from "@plugins/ads/frontend/lib/format";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

interface Props {
  campaign: AdsCampaign;
}

/** Probabilidad de cierre por estado — refleja confianza creciente hacia "ganado". */
const PIPELINE_WEIGHT: Record<AdsState, number> = {
  no_reply: 0,
  nuevo: 0.15,
  activo: 0.30,
  calificado: 0.55,
  cotizado: 0.75,
  ganado: 1,
  perdido: 0,
};

export function AdsStateDistribution({ campaign }: Props) {
  const total = totalConversations(campaign);
  const conversations = campaign.conversations;
  const avgTicket = campaign.avgTicket;

  // Sin counts conversacionales no podemos pintar la distribución honesta.
  // Empty state explícito en lugar de barra vacía.
  if (conversations === null) {
    return (
      <section className="ads-card">
        <header className="ads-card-h">
          <div>
            <h3>Estado de las conversaciones</h3>
            <p>Distribución y valor estimado en pipeline</p>
          </div>
        </header>
        <div
          style={{
            padding: "32px 16px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
            color: "var(--fg-faint)",
          }}
          title="Pendiente: clasificador conversacional + orders link"
        >
          <Icon.dataPending />
          <span style={{ fontSize: 13 }}>
            Distribución pendiente — requiere clasificador conversacional
          </span>
        </div>
      </section>
    );
  }

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Estado de las conversaciones</h3>
          <p>
            Distribución y valor estimado en pipeline · ticket promedio{" "}
            {avgTicket !== null ? fmtMoney(avgTicket) : <MissingField />}
          </p>
        </div>
        <span className="ads-card-meta">{fmtN(total)} chats</span>
      </header>

      <div className="state-bar">
        {ADS_STATE_ORDER.map((s) => {
          const n = conversations[s] || 0;
          if (!n) return null;
          const pct = total > 0 ? (n / total) * 100 : 0;
          return (
            <div
              key={s}
              className="state-seg"
              style={{ flex: n, background: ADS_STATES[s].color }}
              title={`${ADS_STATES[s].label}: ${n} (${pct.toFixed(1)}%)`}
            >
              {pct > 6 && <span>{Math.round(pct)}%</span>}
            </div>
          );
        })}
      </div>

      <div className="state-legend">
        {ADS_STATE_ORDER.map((s) => {
          const n = conversations[s] || 0;
          const pct = total > 0 ? (n / total) * 100 : 0;
          // Sin avgTicket no podemos calcular el valor estimado. Mostramos
          // <MissingField /> en lugar de "—" para que se distinga "0 reales"
          // de "dato faltante".
          const canValue = avgTicket !== null;
          const val = canValue ? n * avgTicket * PIPELINE_WEIGHT[s] : 0;
          const label =
            s === "ganado"
              ? "Ingresos "
              : s === "no_reply" || s === "perdido"
              ? ""
              : "Pipeline ";
          return (
            <div key={s} className="state-leg">
              <div className="sl-h">
                <span
                  className="sl-dot"
                  style={{ background: ADS_STATES[s].color }}
                />
                <span className="sl-lbl">{ADS_STATES[s].label}</span>
              </div>
              <div className="sl-v">
                {fmtN(n)} <span className="sl-pct">· {pct.toFixed(1)}%</span>
              </div>
              <div className="sl-val">
                {label}
                {!canValue ? (
                  <MissingField />
                ) : val ? (
                  fmtMoneyK(val)
                ) : (
                  "—"
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
