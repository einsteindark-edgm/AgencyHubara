/**
 * Header del canvas Ads: identificación de la campaña + KPIs principales
 * (Ingresos, ROAS, Inversión, Chats, CAC, % Ganados). El header también
 * incluye el segmented control de rango temporal — el cálculo real del rango
 * es trabajo del backend; hoy es UI presentacional.
 */

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

interface Props {
  campaign: AdsCampaign;
}

export function AdsOverviewHeader({ campaign }: Props) {
  const total = totalConversations(campaign);
  const won = campaign.conversations.ganado || 0;
  const roas = campaign.revenue / campaign.spend;
  const cac = won > 0 ? campaign.spend / won : 0;
  const costPerChat = campaign.spend / campaign.started;
  const winRate = total > 0 ? won / total : 0;

  return (
    <header className="ads-head">
      <div className="ads-head-top">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className={"camp-status lg " + campaign.status}>
              {campaign.status === "active" ? <AdsIcon.play /> : <AdsIcon.pause />}
            </span>
            <h1>{campaign.name}</h1>
            <span className={"camp-pill " + campaign.status}>
              {campaign.status === "active" ? "Activa" : "Pausada"}
            </span>
          </div>
          <p className="ads-head-sub">
            <span>
              <AdsIcon.cal /> {campaign.dates}
            </span>
            <span className="dot-sep">·</span>
            <span>
              <AdsIcon.loc /> {campaign.audience}
            </span>
            <span className="dot-sep">·</span>
            <span>
              <AdsIcon.meta /> ID {campaign.metaCampaignId}
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
          value={fmtMoneyK(campaign.revenue)}
          sub={`ROAS ${roas.toFixed(2)}× · ${won} ventas`}
        />
        <Kpi
          tone="accent"
          label="ROAS"
          value={roas.toFixed(2) + "×"}
          sub={`${fmtMoneyK(campaign.revenue)} / ${fmtMoneyK(campaign.spend)}`}
        />
        <Kpi
          label="Inversión"
          value={fmtMoneyK(campaign.spend)}
          sub={`${campaign.daysRun} días · ${fmtMoneyK(campaign.spend / campaign.daysRun)}/día`}
        />
        <Kpi
          label="Chats iniciados"
          value={fmtN(campaign.started)}
          sub={`Costo por chat ${fmtMoney(Math.round(costPerChat))}`}
        />
        <Kpi
          label="CAC"
          value={cac > 0 ? fmtMoney(Math.round(cac)) : "—"}
          sub={`Ticket promedio ${fmtMoneyK(campaign.avgTicket)}`}
        />
        <Kpi
          tone="green"
          label="% Ganados"
          value={fmtPct(winRate, 1)}
          sub={`${won} de ${total} chats`}
        />
      </div>
    </header>
  );
}

interface KpiProps {
  label: string;
  value: string;
  sub?: string;
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
