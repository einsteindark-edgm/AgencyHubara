/**
 * Inspector derecho de Ads: detalle de la campaña seleccionada — creativo del
 * anuncio (preview WhatsApp CTWA), métricas Meta (CTR/CPM/CPC/CAC), embudo
 * WhatsApp (tasas conversacionales) y sugerencias generadas por el agente IA
 * (mock — eventualmente vendrían de un job que analiza la performance).
 *
 * Casi todos los campos derivados dependen de inputs del backend que aún no
 * se integran (Meta Ads API para impressions/clicks/spend, clasificador para
 * conversations counts, orders para avgTicket). Cuando un input requerido es
 * `null`, la celda muestra `<MissingField />`. Las sugerencias del agente IA
 * siguen siendo mock (no parte del scope inicial).
 */

import type { ReactNode } from "react";

import {
  totalConversations,
  type AdsCampaign,
} from "@/entities/ads-campaign";
import { Icon } from "@/shared/ui";

import { fmtMoney, fmtN, fmtPct } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

interface Props {
  campaign: AdsCampaign;
}

/** Helper: renderiza `fmtFn(value)` si value != null, sino `<MissingField />`. */
function nv<T>(value: T | null, fmtFn: (v: T) => ReactNode): ReactNode {
  return value !== null && value !== undefined ? fmtFn(value) : <MissingField />;
}

export function AdsInspector({ campaign }: Props) {
  const c = campaign;
  const total = totalConversations(c);
  const conv = c.conversations;
  const won = conv?.ganado ?? null;

  // Métricas derivadas — todas requieren TODOS sus inputs no-null. Si falta
  // alguno, el cálculo queda `null` y la celda renderiza <MissingField />.
  const ctr =
    c.impressions !== null && c.impressions > 0 && c.clicks !== null
      ? c.clicks / c.impressions
      : null;
  const startedRate =
    c.clicks !== null && c.clicks > 0 ? c.started / c.clicks : null;
  const winRate = won !== null && total > 0 ? won / total : null;
  const replyRate =
    conv && total > 0 ? 1 - (conv.no_reply || 0) / total : null;
  const qualifiedRate =
    conv && total > 0
      ? ((conv.calificado || 0) + (conv.cotizado || 0) + (won ?? 0)) / total
      : null;
  const cpm =
    c.impressions !== null && c.impressions > 0 && c.spend !== null
      ? Math.round((c.spend / c.impressions) * 1000)
      : null;
  const cpc =
    c.clicks !== null && c.clicks > 0 && c.spend !== null
      ? Math.round(c.spend / c.clicks)
      : null;
  const costPerChat =
    c.spend !== null && c.started > 0 ? Math.round(c.spend / c.started) : null;
  const cpa =
    won !== null && won > 0 && c.spend !== null ? Math.round(c.spend / won) : null;

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
            {c.name ?? <MissingField />}
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2 }}>
            {c.objective ?? <MissingField />} ·{" "}
            {c.placement ?? <MissingField />}
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
              <div className="ad-pb-headline">
                {c.creativeTitle ?? <MissingField withIcon />}
              </div>
              <button className="ad-pb-cta">
                <AdsIcon.wa />
                Enviar mensaje
              </button>
            </div>
          </div>
          <Row label="Plantilla apertura" mono>
            {c.template ?? <MissingField />}
          </Row>
          <Row label="Campaign ID" mono>
            {c.metaCampaignId ?? <MissingField />}
          </Row>
          <Row label="Ad set">{c.adSet ?? <MissingField />}</Row>
          <button className="insp-button full" style={{ marginTop: 8 }}>
            <AdsIcon.ext />
            Abrir en Meta Ads Manager
          </button>
        </StaticPanel>

        <StaticPanel title="Métricas de Meta">
          <Row label="Impresiones">{nv(c.impressions, fmtN)}</Row>
          <Row label="Alcance">{nv(c.reach, fmtN)}</Row>
          <Row label="Clics">{nv(c.clicks, fmtN)}</Row>
          <Row label="CTR">{nv(ctr, (v) => fmtPct(v, 2))}</Row>
          <Row label="CPM">{nv(cpm, fmtMoney)}</Row>
          <Row label="CPC">{nv(cpc, fmtMoney)}</Row>
          <Row label="Costo por chat">{nv(costPerChat, fmtMoney)}</Row>
          <Row label="Costo por cliente (CAC)">
            {nv(cpa, fmtMoney)}
          </Row>
        </StaticPanel>

        <StaticPanel title="WhatsApp · embudo">
          <Row label="Clic → chat iniciado">
            {nv(startedRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="Respuesta a plantilla">
            {nv(replyRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="Tiempo a 1ª respuesta">
            {c.firstResp ?? <MissingField />}
          </Row>
          <Row label="% calificados">
            {nv(qualifiedRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="% ganados" valueStyle={{ color: "var(--green)" }}>
            {nv(winRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="Ticket promedio">{nv(c.avgTicket, fmtMoney)}</Row>
        </StaticPanel>

        <StaticPanel title="Sugerencias del agente IA">
          {/* Mock — el job que las genera no es parte del scope inicial. */}
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
