/**
 * Sidebar de Ads: lista de campañas Meta filtrable por estado (Activas /
 * Pausadas / Todas) con barra micro-apilada de distribución de estados
 * conversacionales.
 */

import { useMemo, useState } from "react";

import {
  ADS_STATES,
  ADS_STATE_ORDER,
  totalConversations,
  type AdsCampaign,
  type AdsConversationCounts,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import { fmtMoneyK, fmtN } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

type StatusFilter = "active" | "paused" | "all";

interface Props {
  campaigns: AdsCampaign[];
  selected: string;
  onSelect: (id: string) => void;
}

export function AdsCampaignsList({ campaigns, selected, onSelect }: Props) {
  const counts = useMemo(
    () => ({
      active: campaigns.filter((c) => c.status === "active").length,
      paused: campaigns.filter((c) => c.status === "paused").length,
      all: campaigns.length,
    }),
    [campaigns],
  );

  // Default filter: si no tenemos `status` de Meta Ads API todavía (todas
  // las campañas vienen con `status: null`), arrancamos en "all" — sino el
  // usuario vería 0 resultados con el filtro "Activas" por default.
  const defaultFilter: StatusFilter =
    counts.active === 0 && counts.paused === 0 ? "all" : "active";
  const [filter, setFilter] = useState<StatusFilter>(defaultFilter);

  const list = useMemo(() => {
    if (filter === "all") return campaigns;
    return campaigns.filter((c) => c.status === filter);
  }, [campaigns, filter]);

  const filters: { key: StatusFilter; label: string; n: number }[] = [
    { key: "active", label: "Activas", n: counts.active },
    { key: "paused", label: "Pausadas", n: counts.paused },
    { key: "all", label: "Todas", n: counts.all },
  ];

  return (
    <aside className="sidebar ads-sidebar">
      <div className="side-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Campañas Meta
          </span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 2 }}>
            <button
              className="tb-btn"
              style={{ width: 22, height: 22 }}
              title="Sincronizar con Meta"
            >
              <Icon.refresh />
            </button>
            <button
              className="tb-btn"
              style={{ width: 22, height: 22 }}
              title="Filtrar"
            >
              <Icon.filter />
            </button>
          </span>
        </div>

        <div className="side-search">
          <Icon.search />
          <input placeholder="Buscar campaña…" />
        </div>

        <div className="side-tabs">
          {filters.map((f) => (
            <button
              key={f.key}
              className={"pill" + (filter === f.key ? " on" : "")}
              onClick={() => setFilter(f.key)}
            >
              {f.label} <span className="ct">{f.n}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="side-list">
        {list.map((c) => (
          <CampaignRow
            key={c.id}
            campaign={c}
            selected={selected === c.id}
            onSelect={onSelect}
          />
        ))}
      </div>
    </aside>
  );
}

interface RowProps {
  campaign: AdsCampaign;
  selected: boolean;
  onSelect: (id: string) => void;
}

function CampaignRow({ campaign, selected, onSelect }: RowProps) {
  const total = totalConversations(campaign);
  // ROAS requiere spend Y revenue. Si falta cualquiera, NO podemos calcularlo
  // — la cell muestra `<MissingField />` y el campo no influye en el color
  // semaforo. Cuando lleguen Meta Ads API y orders, el cálculo se reactiva.
  const roas =
    campaign.revenue !== null && campaign.spend !== null && campaign.spend > 0
      ? campaign.revenue / campaign.spend
      : null;
  const [from, to] = campaign.dates.split("→").map((s) => s.trim());

  return (
    <button
      className={"camp-row" + (selected ? " sel" : "")}
      onClick={() => onSelect(campaign.id)}
    >
      <div className="camp-row-h">
        {/* Status: Meta Ads API aún no integrada → null → marker visual. */}
        {campaign.status === "active" ? (
          <span className="camp-status active" title="Activa">
            <AdsIcon.play />
          </span>
        ) : campaign.status === "paused" ? (
          <span className="camp-status paused" title="Pausada">
            <AdsIcon.pause />
          </span>
        ) : (
          <span
            className="camp-status"
            title="Estado pendiente — Meta Ads API"
            style={{ opacity: 0.6 }}
          >
            <Icon.dataPending />
          </span>
        )}
        <span className="camp-name">
          {campaign.name ?? <MissingField />}
        </span>
      </div>
      <div className="camp-row-meta">
        <span className="camp-dates">
          {from} → {to ?? "—"}
        </span>
      </div>
      <div className="camp-row-stats">
        <div className="crs">
          <span className="crs-l">Inversión</span>
          <span className="crs-v">
            {campaign.spend !== null ? (
              fmtMoneyK(campaign.spend)
            ) : (
              <MissingField withIcon />
            )}
          </span>
        </div>
        <div className="crs">
          <span className="crs-l">Chats</span>
          <span className="crs-v">{fmtN(total)}</span>
        </div>
        <div className="crs">
          <span className="crs-l">ROAS</span>
          <span
            className={
              "crs-v " +
              (roas === null
                ? ""
                : roas >= 2
                  ? "pos"
                  : roas >= 1
                    ? "neu"
                    : "neg")
            }
          >
            {roas !== null ? `${roas.toFixed(1)}×` : <MissingField withIcon />}
          </span>
        </div>
      </div>
      <StateMicroBar conversations={campaign.conversations} total={total} />
    </button>
  );
}

interface MicroBarProps {
  /**
   * Counts por estado conversacional. `null` cuando el backend aún no tiene
   * clasificador downstream — la barra no se renderiza en ese caso (la
   * card sigue mostrando el contador de `Chats`).
   */
  conversations: AdsConversationCounts | null;
  total: number;
}

function StateMicroBar({ conversations, total }: MicroBarProps) {
  if (!total || !conversations) return null;
  return (
    <div className="camp-microbar" title="Distribución de estados">
      {ADS_STATE_ORDER.map((s) => {
        const n = conversations[s] || 0;
        if (!n) return null;
        return (
          <span
            key={s}
            style={{ flex: n, background: ADS_STATES[s].color }}
          />
        );
      })}
    </div>
  );
}
