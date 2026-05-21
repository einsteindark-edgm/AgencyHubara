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

type StatusFilter = "active" | "paused" | "all";

interface Props {
  campaigns: AdsCampaign[];
  selected: string;
  onSelect: (id: string) => void;
}

export function AdsCampaignsList({ campaigns, selected, onSelect }: Props) {
  const [filter, setFilter] = useState<StatusFilter>("active");

  const counts = useMemo(
    () => ({
      active: campaigns.filter((c) => c.status === "active").length,
      paused: campaigns.filter((c) => c.status === "paused").length,
      all: campaigns.length,
    }),
    [campaigns],
  );

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
  const roas = campaign.revenue / campaign.spend;
  const [from, to] = campaign.dates.split("→").map((s) => s.trim());

  return (
    <button
      className={"camp-row" + (selected ? " sel" : "")}
      onClick={() => onSelect(campaign.id)}
    >
      <div className="camp-row-h">
        <span
          className={"camp-status " + campaign.status}
          title={campaign.status === "active" ? "Activa" : "Pausada"}
        >
          {campaign.status === "active" ? <AdsIcon.play /> : <AdsIcon.pause />}
        </span>
        <span className="camp-name">{campaign.name}</span>
      </div>
      <div className="camp-row-meta">
        <span className="camp-dates">
          {from} → {to ?? "—"}
        </span>
      </div>
      <div className="camp-row-stats">
        <div className="crs">
          <span className="crs-l">Inversión</span>
          <span className="crs-v">{fmtMoneyK(campaign.spend)}</span>
        </div>
        <div className="crs">
          <span className="crs-l">Chats</span>
          <span className="crs-v">{fmtN(total)}</span>
        </div>
        <div className="crs">
          <span className="crs-l">ROAS</span>
          <span
            className={
              "crs-v " + (roas >= 2 ? "pos" : roas >= 1 ? "neu" : "neg")
            }
          >
            {roas.toFixed(1)}×
          </span>
        </div>
      </div>
      <StateMicroBar conversations={campaign.conversations} total={total} />
    </button>
  );
}

interface MicroBarProps {
  conversations: AdsConversationCounts;
  total: number;
}

function StateMicroBar({ conversations, total }: MicroBarProps) {
  if (!total) return null;
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
