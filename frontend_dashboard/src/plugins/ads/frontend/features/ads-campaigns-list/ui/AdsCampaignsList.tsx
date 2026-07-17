/**
 * Sidebar de Ads: lista de campañas Meta filtrable por estado (Activas /
 * Pausadas / Todas) con barra micro-apilada de distribución de estados
 * conversacionales.
 *
 * Segmentación (2026-07-10): cada campaña resuelta (con `metaCampaignId`)
 * tiene un chevron que despliega sus segmentos (ad sets) — fetch lazy vía
 * `useCampaignAdsets` al expandir. Seleccionar un segmento scopea el canvas
 * central (el Page recibe `onSelectAdset`).
 */

import { useMemo, useState } from "react";

import {
  ADS_STATES,
  ADS_STATE_ORDER,
  totalConversations,
  useCampaignAdsets,
  type AdsCampaign,
  type AdsConversationCounts,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import { fmtMoneyK, fmtN, fmtUsd } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

type StatusFilter = "active" | "paused" | "all";

/** Ventana por defecto para el fetch de segmentos cuando el Page no la pasa
 *  (tests / uso suelto): sin límite (total). El Page SIEMPRE la pasa. */
const OPEN_WINDOW: AdsWindowParams = { days: null, from: null, to: null };

interface Props {
  campaigns: AdsCampaign[];
  selected: string;
  onSelect: (id: string) => void;
  /** Ventana activa del dashboard — los segmentos agregan sobre ella. */
  params?: AdsWindowParams;
  /** Segmento seleccionado (scope del canvas) o null = campaña completa. */
  selectedAdsetId?: string | null;
  /** Selección de segmento: `(campaignId, adsetId|null)`. */
  onSelectAdset?: (campaignId: string, adsetId: string | null) => void;
}

export function AdsCampaignsList({
  campaigns,
  selected,
  onSelect,
  params = OPEN_WINDOW,
  selectedAdsetId = null,
  onSelectAdset,
}: Props) {
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
            params={params}
            selectedAdsetId={selected === c.id ? selectedAdsetId : null}
            onSelectAdset={onSelectAdset}
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
  params: AdsWindowParams;
  selectedAdsetId: string | null;
  onSelectAdset?: (campaignId: string, adsetId: string | null) => void;
}

function CampaignRow({
  campaign,
  selected,
  onSelect,
  params,
  selectedAdsetId,
  onSelectAdset,
}: RowProps) {
  // Desplegable de segmentos: solo campañas resueltas contra Meta tienen
  // jerarquía (direct / sin resolver no tienen adsets conocidos).
  const [expanded, setExpanded] = useState(false);
  const canExpand = campaign.metaCampaignId !== null;
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
    <>
    {/* Wrapper relativo: el chevron es un botón HERMANO (no descendiente) de
        la card — un control interactivo dentro de un <button> es HTML
        inválido y confunde a screen readers (hallazgo review 2026-07-10). */}
    <div style={{ position: "relative" }}>
    <button
      className={"camp-row" + (selected && !selectedAdsetId ? " sel" : "")}
      style={canExpand ? { paddingRight: 28 } : undefined}
      onClick={() => {
        onSelect(campaign.id);
        // Seleccionar la campaña resetea el scope de segmento.
        onSelectAdset?.(campaign.id, null);
      }}
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
        {campaign.sourceType === "hubara_campaign" && (
          <span
            title="Campaña directa de WhatsApp (sección Marketing)"
            style={{
              fontSize: 9,
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: 999,
              background: "var(--color-ok-soft)",
              color: "var(--color-ok)",
              flexShrink: 0,
            }}
          >
            WhatsApp
          </span>
        )}
      </div>
      <div className="camp-row-meta">
        <span className="camp-dates">
          {from} → {to ?? "—"}
        </span>
      </div>
      <div
        className="camp-row-stats"
        style={{ gridTemplateColumns: "repeat(2, 1fr)" }}
      >
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
        <div className="crs">
          <span className="crs-l">Costo LLM</span>
          <span className="crs-v">
            {campaign.llmCostUsd !== null ? (
              fmtUsd(campaign.llmCostUsd)
            ) : (
              <MissingField withIcon />
            )}
          </span>
        </div>
        <div className="crs">
          <span className="crs-l">CAPI</span>
          <CapiSignal campaign={campaign} />
        </div>
      </div>
      <StateMicroBar conversations={campaign.conversations} total={total} />
    </button>
    {canExpand && (
      <button
        type="button"
        aria-label={`Ver segmentos de ${campaign.name ?? campaign.id}`}
        aria-expanded={expanded}
        className="tb-btn"
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          width: 20,
          height: 20,
          transform: expanded ? undefined : "rotate(-90deg)",
          transition: "transform 120ms",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        <Icon.caret />
      </button>
    )}
    </div>
    {expanded && (
      <CampaignSegments
        campaign={campaign}
        params={params}
        selectedAdsetId={selectedAdsetId}
        onSelectAdset={onSelectAdset}
      />
    )}
    </>
  );
}

interface SegmentsProps {
  campaign: AdsCampaign;
  params: AdsWindowParams;
  selectedAdsetId: string | null;
  onSelectAdset?: (campaignId: string, adsetId: string | null) => void;
}

/**
 * Sub-lista de segmentos (ad sets) de una campaña expandida. Fetch lazy:
 * el hook solo dispara cuando el desplegable está abierto (este componente
 * solo se monta expandido). Cada fila muestra el nombre del segmento +
 * chats/inversión, y al click scopea el canvas central.
 */
function CampaignSegments({
  campaign,
  params,
  selectedAdsetId,
  onSelectAdset,
}: SegmentsProps) {
  const { data: segments = [], isLoading } = useCampaignAdsets(
    campaign.id,
    params,
  );

  if (isLoading) {
    return (
      <div className="camp-segments" style={{ padding: "4px 12px 8px 24px" }}>
        <span className="crs-l">Cargando segmentos…</span>
      </div>
    );
  }
  if (!segments.length) {
    return (
      <div className="camp-segments" style={{ padding: "4px 12px 8px 24px" }}>
        <span className="crs-l">Sin segmentos con actividad</span>
      </div>
    );
  }
  return (
    <div className="camp-segments" style={{ padding: "0 8px 8px 20px" }}>
      {segments.map((s) => {
        const sel = selectedAdsetId === s.id;
        return (
          <button
            key={s.id}
            className={"camp-row" + (sel ? " sel" : "")}
            style={{ padding: "6px 8px", marginTop: 4 }}
            onClick={() => onSelectAdset?.(campaign.id, sel ? null : s.id)}
          >
            <div className="camp-row-h">
              <span className="camp-name" style={{ fontSize: 12 }}>
                {s.name ?? s.id}
              </span>
            </div>
            <div
              className="camp-row-stats"
              style={{ gridTemplateColumns: "repeat(2, 1fr)" }}
            >
              <div className="crs">
                <span className="crs-l">Chats</span>
                <span className="crs-v">{fmtN(totalConversations(s))}</span>
              </div>
              <div className="crs">
                <span className="crs-l">Inversión</span>
                <span className="crs-v">
                  {s.spend !== null ? fmtMoneyK(s.spend) : <MissingField withIcon />}
                </span>
              </div>
            </div>
            <StateMicroBar
              conversations={s.conversations}
              total={totalConversations(s)}
            />
          </button>
        );
      })}
    </div>
  );
}

/**
 * Señal CAPI compacta de la campaña: `↑ N` = eventos server-side enviados a
 * Meta (LeadSubmitted + Purchase), con el desglose en el tooltip nativo.
 * `capiFailed > 0` → clase `neg` (rojo del design system, misma que ROAS<1).
 * Todo en 0 → "—" discreto (mismo lenguaje que los campos pendientes).
 */
function CapiSignal({ campaign }: { campaign: AdsCampaign }) {
  const sent = campaign.capiLeadsSent + campaign.capiPurchasesSent;
  if (sent === 0 && campaign.capiFailed === 0) {
    return (
      <span className="crs-v">
        <MissingField title="Sin eventos CAPI reportados a Meta" />
      </span>
    );
  }
  const breakdown = `${campaign.capiLeadsSent} LeadSubmitted · ${campaign.capiPurchasesSent} Purchase · ${campaign.capiFailed} fallos`;
  return (
    <span
      className={"crs-v" + (campaign.capiFailed > 0 ? " neg" : "")}
      title={breakdown}
    >
      ↑ {fmtN(sent)}
    </span>
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
