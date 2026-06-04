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

import { useState, type CSSProperties, type ReactNode } from "react";

import {
  ADS_DATE_RANGES,
  totalConversations,
  type AdsCampaign,
  type AdsDateRange,
  type AdsRangeSelection,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import {
  fmtMoney,
  fmtMoneyK,
  fmtN,
  fmtPct,
  fmtUsd,
} from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

interface Props {
  campaign: AdsCampaign;
  /** Selección de ventana (preset 7d/30d/90d/total o rango custom fecha
   *  inicio→fin), controlada por la Page. */
  selection: AdsRangeSelection;
  onSelectionChange: (s: AdsRangeSelection) => void;
}

export function AdsOverviewHeader({
  campaign,
  selection,
  onSelectionChange,
}: Props) {
  // Borrador local de las fechas custom (los <input type="date">). La selección
  // APLICADA vive en la Page; acá solo manejamos la edición en curso y la
  // aplicamos cuando AMBAS fechas están puestas. Elegir un preset las limpia.
  const [from, setFrom] = useState(
    selection.kind === "custom" ? selection.from : "",
  );
  const [to, setTo] = useState(selection.kind === "custom" ? selection.to : "");
  const isPreset = selection.kind === "preset";

  function pickPreset(p: AdsDateRange) {
    setFrom("");
    setTo("");
    onSelectionChange({ kind: "preset", preset: p });
  }
  function pickFrom(v: string) {
    setFrom(v);
    if (v && to) onSelectionChange({ kind: "custom", from: v, to });
  }
  function pickTo(v: string) {
    setTo(v);
    if (from && v) onSelectionChange({ kind: "custom", from, to: v });
  }

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
            {ADS_DATE_RANGES.map((r) => (
              <button
                key={r.key}
                className={isPreset && selection.preset === r.key ? "on" : ""}
                onClick={() => pickPreset(r.key)}
              >
                {r.label}
              </button>
            ))}
          </div>
          {/* Rango exacto (fecha inicio → fecha fin). Se aplica al tener ambas
              fechas; elegir un preset lo limpia. `min`/`max` impiden invertirlo. */}
          <div
            className="ads-range-dates"
            title="Rango personalizado (fecha inicio → fecha fin)"
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <input
              type="date"
              aria-label="Fecha inicio"
              value={from}
              max={to || undefined}
              onChange={(e) => pickFrom(e.target.value)}
              style={dateInputStyle(!isPreset)}
            />
            <span style={{ opacity: 0.5, fontSize: 12 }}>→</span>
            <input
              type="date"
              aria-label="Fecha fin"
              value={to}
              min={from || undefined}
              onChange={(e) => pickTo(e.target.value)}
              style={dateInputStyle(!isPreset)}
            />
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

      {/* 7 KPIs — override inline del grid (la clase base es repeat(6,1fr) y
          el 7º caería solo en una 2ª fila). */}
      <div className="ads-kpis" style={{ gridTemplateColumns: "repeat(7, 1fr)" }}>
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
        <Kpi
          label="Costo LLM"
          value={
            campaign.llmCostUsd !== null ? (
              fmtUsd(campaign.llmCostUsd)
            ) : (
              <MissingField withIcon />
            )
          }
          sub={
            campaign.llmTokens !== null
              ? `${fmtN(campaign.llmTokens)} tokens`
              : "— tokens"
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

/** Estilo inline del `<input type="date">` del rango custom (dark; resalta el
 *  borde cuando el rango custom está activo). Inline para no tocar el bloque
 *  `@theme` de index.css (spinal file). */
function dateInputStyle(active: boolean): CSSProperties {
  return {
    background: "rgba(255,255,255,0.04)",
    border: active ? "1px solid #5fa9ff" : "1px solid rgba(255,255,255,0.14)",
    borderRadius: 8,
    color: "inherit",
    font: "inherit",
    fontSize: 12,
    padding: "5px 8px",
    colorScheme: "dark",
  };
}
