/**
 * Inspector derecho de Ads: detalle de la campaña seleccionada — creativo del
 * anuncio (preview WhatsApp CTWA), métricas Meta (CTR/CPM/CPC/CAC), embudo
 * WhatsApp (tasas conversacionales) y sugerencias generadas por el agente IA
 * (mock — eventualmente vendrían de un job que analiza la performance).
 */

import {
  totalConversations,
  type AdsCampaign,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import { fmtMoney, fmtN, fmtPct } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";

interface Props {
  campaign: AdsCampaign;
}

export function AdsInspector({ campaign }: Props) {
  const c = campaign;
  const total = totalConversations(c);
  const won = c.conversations.ganado || 0;
  const ctr = c.impressions > 0 ? c.clicks / c.impressions : 0;
  const startedRate = c.clicks > 0 ? c.started / c.clicks : 0;
  const winRate = total > 0 ? won / total : 0;
  const replyRate = total > 0 ? 1 - (c.conversations.no_reply || 0) / total : 0;
  const qualifiedRate =
    total > 0
      ? ((c.conversations.calificado || 0) +
          (c.conversations.cotizado || 0) +
          won) /
        total
      : 0;
  const cpm = c.impressions > 0 ? Math.round((c.spend / c.impressions) * 1000) : 0;
  const cpc = c.clicks > 0 ? Math.round(c.spend / c.clicks) : 0;
  const costPerChat = c.started > 0 ? Math.round(c.spend / c.started) : 0;
  const cpa = won > 0 ? Math.round(c.spend / won) : 0;

  return (
    <aside className="inspector">
      <div className="insp-tabs">
        <button className="insp-tab on" title="Detalle">
          <Icon.tag />
        </button>
        <button className="insp-tab" title="Creativo">
          <AdsIcon.ad />
        </button>
        <button className="insp-tab" title="Optimización">
          <AdsIcon.trend />
        </button>
      </div>

      <div className="insp-body">
        <div style={{ padding: "12px 14px 8px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>
            {c.name}
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2 }}>
            {c.objective} · {c.placement}
          </div>
        </div>

        <StaticPanel title="Creativo del anuncio">
          <div className="ad-preview">
            <div className="ad-preview-img">
              <span>Imagen del anuncio</span>
              <span className="ad-pi-sub">1080 × 1350 · vertical</span>
            </div>
            <div className="ad-preview-body">
              <div className="ad-pb-brand">
                <span className="ad-pb-avatar">A</span>
                <div>
                  <div className="ad-pb-name">Aromas · Tienda</div>
                  <div className="ad-pb-spons">
                    Patrocinado · <AdsIcon.meta />
                  </div>
                </div>
              </div>
              <div className="ad-pb-headline">{c.creativeTitle}</div>
              <button className="ad-pb-cta">
                <AdsIcon.wa />
                Enviar mensaje
              </button>
            </div>
          </div>
          <Row label="Plantilla apertura" mono>
            {c.template}
          </Row>
          <Row label="Campaign ID" mono>
            {c.metaCampaignId}
          </Row>
          <Row label="Ad set">{c.adSet}</Row>
          <button className="insp-button full" style={{ marginTop: 8 }}>
            <AdsIcon.ext />
            Abrir en Meta Ads Manager
          </button>
        </StaticPanel>

        <StaticPanel title="Métricas de Meta">
          <Row label="Impresiones">{fmtN(c.impressions)}</Row>
          <Row label="Alcance">{fmtN(c.reach)}</Row>
          <Row label="Clics">{fmtN(c.clicks)}</Row>
          <Row label="CTR">{fmtPct(ctr, 2)}</Row>
          <Row label="CPM">{fmtMoney(cpm)}</Row>
          <Row label="CPC">{fmtMoney(cpc)}</Row>
          <Row label="Costo por chat">{fmtMoney(costPerChat)}</Row>
          <Row label="Costo por cliente (CAC)">
            {won > 0 ? fmtMoney(cpa) : "—"}
          </Row>
        </StaticPanel>

        <StaticPanel title="WhatsApp · embudo">
          <Row label="Clic → chat iniciado">{fmtPct(startedRate, 1)}</Row>
          <Row label="Respuesta a plantilla">{fmtPct(replyRate, 1)}</Row>
          <Row label="Tiempo a 1ª respuesta">{c.firstResp}</Row>
          <Row label="% calificados">{fmtPct(qualifiedRate, 1)}</Row>
          <Row label="% ganados" valueStyle={{ color: "var(--green)" }}>
            {fmtPct(winRate, 1)}
          </Row>
          <Row label="Ticket promedio">{fmtMoney(c.avgTicket)}</Row>
        </StaticPanel>

        <StaticPanel title="Sugerencias del agente IA">
          <div className="ads-tip">
            <span
              className="ads-tip-ico"
              style={{
                background: "rgba(91,224,123,0.18)",
                color: "#5be07b",
              }}
            >
              <AdsIcon.trend />
            </span>
            <div>
              <b>ROAS sobre 3×</b>
              <p>
                Esta campaña convierte mejor que el promedio. Considera
                aumentar 20% el presupuesto diario.
              </p>
            </div>
          </div>
          <div className="ads-tip">
            <span
              className="ads-tip-ico"
              style={{
                background: "rgba(255,180,74,0.18)",
                color: "#ffb44a",
              }}
            >
              <AdsIcon.info />
            </span>
            <div>
              <b>30% sin respuesta tras clic</b>
              <p>
                Prueba una plantilla de apertura más conversacional y reduce
                el tiempo a 1ª respuesta del bot.
              </p>
            </div>
          </div>
        </StaticPanel>
      </div>
    </aside>
  );
}

/* ── Panel estático (no usa shared/ui/Panel porque ese maneja open/close;
 *    acá queremos siempre abierto + caret estético) ──────────────────────── */
interface PanelProps {
  title: string;
  children: React.ReactNode;
}

function StaticPanel({ title, children }: PanelProps) {
  return (
    <div className="panel">
      <div className="panel-h">
        <span className="caret">
          <Icon.caret />
        </span>
        <span className="ttl">{title}</span>
      </div>
      <div className="panel-c">{children}</div>
    </div>
  );
}

interface RowProps {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
  valueStyle?: React.CSSProperties;
}

function Row({ label, children, mono, valueStyle }: RowProps) {
  return (
    <div className="form-row">
      <span className="lbl">{label}</span>
      <span
        className={"val" + (mono ? " mono" : "")}
        style={valueStyle}
      >
        {children}
      </span>
    </div>
  );
}
