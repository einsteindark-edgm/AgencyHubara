/**
 * Header del canvas Ads: identificación de la campaña + KPIs principales
 * (Ingresos, ROAS, Inversión, Chats, CAC, % Ganados). El header también
 * incluye el segmented control de rango temporal — el cálculo real del rango
 * es trabajo del backend; hoy es UI presentacional.
 *
 * Muchos KPIs dependen de fields del backend que aún no se integran (spend,
 * revenue, conversations counts, avgTicket, daysRun, audience,
 * metaCampaignId). Cuando vienen `null`, el slot muestra `<MissingField />`
 * con tooltip explicativo en lugar de calcular sobre null.
 */

import type { ReactNode } from "react";

import {
  totalConversations,
  type AdsCampaign,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import {
  fmtMoney,
  fmtMoneyK,
  fmtN,
  fmtPct,
} from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

interface Props {
  campaign: AdsCampaign;
}

export function AdsOverviewHeader({ campaign }: Props) {
  const total = totalConversations(campaign);
  const won = campaign.conversations?.ganado ?? null;

  // Derivados: cada uno requiere TODOS sus inputs no-null. Si alguno falta,
  // el cálculo queda `null` y el slot renderiza <MissingField />.
  const roas =
    campaign.revenue !== null && campaign.spend !== null && campaign.spend > 0
      ? campaign.revenue / campaign.spend
      : null;
  const cac =
    won !== null && won > 0 && campaign.spend !== null
      ? campaign.spend / won
      : null;
  const costPerChat =
    campaign.spend !== null && campaign.started > 0
      ? campaign.spend / campaign.started
      : null;
  const winRate = won !== null && total > 0 ? won / total : null;
  const spendPerDay =
    campaign.spend !== null && campaign.daysRun !== null && campaign.daysRun > 0
      ? campaign.spend / campaign.daysRun
      : null;

  return (
    <header className="ads-head">
      <div className="ads-head-top">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {campaign.status === "active" ? (
              <span className="camp-status lg active">
                <AdsIcon.play />
              </span>
            ) : campaign.status === "paused" ? (
              <span className="camp-status lg paused">
                <AdsIcon.pause />
              </span>
            ) : (
              <span
                className="camp-status lg"
                title="Estado pendiente — Meta Ads API"
                style={{ opacity: 0.6 }}
              >
                <Icon.dataPending />
              </span>
            )}
            <h1>{campaign.name ?? <MissingField />}</h1>
            {campaign.status ? (
              <span className={"camp-pill " + campaign.status}>
                {campaign.status === "active" ? "Activa" : "Pausada"}
              </span>
            ) : null}
          </div>
          <p className="ads-head-sub">
            <span>
              <AdsIcon.cal /> {campaign.dates}
            </span>
            <span className="dot-sep">·</span>
            <span>
              <AdsIcon.loc /> {campaign.audience ?? <MissingField />}
            </span>
            <span className="dot-sep">·</span>
            <span>
              <AdsIcon.meta /> ID {campaign.metaCampaignId ?? <MissingField />}
            </span>
          </p>
        </div>
        <div className="ads-head-actions">
          <div className="ads-range-seg">
            <button>7d</button>
            <button className="on">14d</button>
            <button>30d</button>
            <button>Total</button>
          </div>
          <button className="insp-button">
            <Icon.refresh />
            Sincronizar
          </button>
          <button className="insp-button">
            <AdsIcon.ext />
            Abrir en Meta
          </button>
        </div>
      </div>

      <div className="ads-kpis">
        <Kpi
          hero
          tone="green"
          label="Ingresos atribuidos"
          value={
            campaign.revenue !== null ? (
              fmtMoneyK(campaign.revenue)
            ) : (
              <MissingField withIcon />
            )
          }
          sub={
            <>
              ROAS {roas !== null ? `${roas.toFixed(2)}×` : <MissingField />}
              {won !== null ? ` · ${won} ventas` : ""}
            </>
          }
        />
        <Kpi
          tone="accent"
          label="ROAS"
          value={
            roas !== null ? `${roas.toFixed(2)}×` : <MissingField withIcon />
          }
          sub={
            campaign.revenue !== null && campaign.spend !== null ? (
              `${fmtMoneyK(campaign.revenue)} / ${fmtMoneyK(campaign.spend)}`
            ) : (
              <MissingField />
            )
          }
        />
        <Kpi
          label="Inversión"
          value={
            campaign.spend !== null ? (
              fmtMoneyK(campaign.spend)
            ) : (
              <MissingField withIcon />
            )
          }
          sub={
            campaign.daysRun !== null && spendPerDay !== null ? (
              `${campaign.daysRun} días · ${fmtMoneyK(spendPerDay)}/día`
            ) : (
              <MissingField />
            )
          }
        />
        <Kpi
          label="Chats iniciados"
          value={fmtN(campaign.started)}
          sub={
            costPerChat !== null ? (
              `Costo por chat ${fmtMoney(Math.round(costPerChat))}`
            ) : (
              <>
                Costo por chat <MissingField />
              </>
            )
          }
        />
        <Kpi
          label="CAC"
          value={
            cac !== null && cac > 0 ? (
              fmtMoney(Math.round(cac))
            ) : (
              <MissingField withIcon />
            )
          }
          sub={
            <>
              Ticket promedio{" "}
              {campaign.avgTicket !== null ? (
                fmtMoneyK(campaign.avgTicket)
              ) : (
                <MissingField />
              )}
            </>
          }
        />
        <Kpi
          tone="green"
          label="% Ganados"
          value={
            winRate !== null ? fmtPct(winRate, 1) : <MissingField withIcon />
          }
          sub={
            won !== null ? (
              `${won} de ${total} chats`
            ) : (
              <>
                <MissingField /> de {total} chats
              </>
            )
          }
        />
      </div>
    </header>
  );
}

interface KpiProps {
  label: string;
  /** Puede ser un string o un ReactNode (e.g. `<MissingField />`). */
  value: ReactNode;
  /** Opcional. Mismo tratamiento que `value`. */
  sub?: ReactNode;
  tone?: "green" | "accent";
  hero?: boolean;
}

function Kpi({ label, value, sub, tone, hero }: KpiProps) {
  const cls =
    "ads-kpi" + (tone ? " tone-" + tone : "") + (hero ? " hero" : "");
  return (
    <div className={cls}>
      <div className="ads-kpi-l">{label}</div>
      <div className="ads-kpi-v">{value}</div>
      {sub && <div className="ads-kpi-s">{sub}</div>}
    </div>
  );
}
