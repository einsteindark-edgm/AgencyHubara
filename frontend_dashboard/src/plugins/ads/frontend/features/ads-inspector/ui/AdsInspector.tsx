/**
 * Inspector derecho de Ads: detalle de la campaña seleccionada — creativo del
 * anuncio (thumbnail REAL vía Graph, o placeholder honesto), métricas Meta
 * (CTR/CPM/CPC/CAC), embudo WhatsApp (tasas conversacionales) y el HISTORIAL
 * VERSIONADO de análisis con IA (2026-07-09): cada corrida de "Analizar con
 * IA" queda guardada con fecha, estado, snapshot de entrada y resultado —
 * acá se listan, más nueva primero. Nada de sugerencias quemadas.
 *
 * Cuando un input requerido es `null`, la celda muestra `<MissingField />`.
 */

import type { ReactNode } from "react";

import {
  totalConversations,
  type AdsCampaign,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { useRuns } from "@plugins/ads/frontend/entities/ad-analysis-run";
import { useMetaConnection } from "@plugins/ads/frontend/entities/meta-connection";
import { Icon } from "@/shared/ui";

import {
  fmtDuration,
  fmtMoney,
  fmtN,
  fmtPct,
  fmtUsd,
} from "@plugins/ads/frontend/lib/format";
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
  const { data: conn } = useMetaConnection();
  const brandName = conn?.accountName ?? null;
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
            {c.creativeThumbnailUrl ? (
              <img
                src={c.creativeThumbnailUrl}
                alt="Creativo del anuncio"
                style={{ width: "100%", borderRadius: 8, display: "block" }}
              />
            ) : (
              <div className="ad-preview-img">
                <span>Vista previa no disponible</span>
                <span className="ad-pi-sub">Meta no expone el creativo de este ad</span>
              </div>
            )}
            <div className="ad-preview-body">
              <div className="ad-pb-brand">
                <span className="ad-pb-avatar">
                  {(brandName ?? "?").slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <div className="ad-pb-name">{brandName ?? <MissingField />}</div>
                  <div className="ad-pb-spons">
                    Patrocinado · <AdsIcon.meta />
                  </div>
                </div>
              </div>
              <div className="ad-pb-headline">
                {c.creativeTitle ?? <MissingField withIcon />}
              </div>
            </div>
          </div>
          <Row label="Plantilla apertura" mono>
            {c.template ?? <MissingField />}
          </Row>
          <Row label="Campaign ID" mono>
            {c.metaCampaignId ?? <MissingField />}
          </Row>
          <Row label="Ad set">{c.adSet ?? <MissingField />}</Row>
          {c.metaCampaignId && (
            <a
              className="insp-button full"
              style={{ marginTop: 8 }}
              href={`https://adsmanager.facebook.com/adsmanager/manage/campaigns?selected_campaign_ids=${c.metaCampaignId}`}
              target="_blank"
              rel="noreferrer"
            >
              <AdsIcon.ext />
              Abrir en Meta Ads Manager
            </a>
          )}
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
          <Row label="Duración media de conversación">
            {nv(c.avgEpisodeDurationMs, fmtDuration)}
          </Row>
          <Row label="% calificados">
            {nv(qualifiedRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="% ganados" valueStyle={{ color: "var(--green)" }}>
            {nv(winRate, (v) => fmtPct(v, 1))}
          </Row>
          <Row label="Ticket promedio">{nv(c.avgTicket, fmtMoney)}</Row>
          <Row label="Costo LLM (total)">{nv(c.llmCostUsd, fmtUsd)}</Row>
        </StaticPanel>

        <StaticPanel title="Análisis IA · historial">
          <AnalysisHistory />
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

/* ── Historial versionado de análisis (2026-07-09) ─────────────────────────
 * Cada corrida de "Analizar con IA" queda persistida (record del run) con su
 * fecha, estado, snapshot de entrada y resultado. Acá se listan como versiones
 * inmutables — comparar qué sugirió el agente con qué números había.
 */

const _STATUS_LABEL: Record<string, string> = {
  pending: "pendiente",
  running: "corriendo",
  awaiting_approval: "esperando aprobación",
  completed: "completado",
  failed: "falló",
};

function _fmtRunDate(ms: number | null): string {
  if (!ms) return "sin fecha";
  return new Date(ms).toLocaleString("es-CO", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function _resultText(result: unknown): string | null {
  if (result == null) return null;
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function AnalysisHistory() {
  const { data: runs = [], isLoading } = useRuns();

  if (isLoading) {
    return (
      <div style={{ fontSize: 12, color: "var(--fg-mute)", padding: "6px 0" }}>
        Cargando historial…
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--fg-mute)", padding: "6px 0" }}>
        Sin análisis todavía — corré uno con «Analizar con IA» y quedará
        guardado acá con fecha, números y sugerencias.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {runs.map((r) => {
        const text = _resultText(r.result);
        return (
          <details
            key={r.runId}
            style={{
              border: "1px solid var(--line)",
              borderRadius: 8,
              padding: "6px 8px",
            }}
          >
            <summary style={{ cursor: "pointer", fontSize: 12, listStyle: "none" }}>
              <b>{_fmtRunDate(r.createdAtMs)}</b>
              {" · "}
              <span
                style={{
                  color:
                    r.status === "completed"
                      ? "var(--green)"
                      : r.status === "failed"
                        ? "var(--red, #e5484d)"
                        : "var(--fg-mute)",
                }}
              >
                {_STATUS_LABEL[r.status] ?? r.status}
              </span>
            </summary>
            <div style={{ marginTop: 6, fontSize: 12 }}>
              {text ? (
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    maxHeight: 220,
                    overflow: "auto",
                    margin: 0,
                  }}
                >
                  {text}
                </pre>
              ) : (
                <span style={{ color: "var(--fg-mute)" }}>
                  Sin resultado {r.status === "failed" ? "(la corrida falló)" : "todavía"}.
                </span>
              )}
            </div>
          </details>
        );
      })}
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
