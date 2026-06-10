/**
 * Embudo horizontal de conversión: del impresión Meta al cliente cerrado por
 * WhatsApp. 6 etapas (impresiones → clics → chats iniciados → en conversación
 * → cotizados → ganados), cada una con su barra proporcional, conversion
 * paso-a-paso y drop entre etapas.
 *
 * El funnel completo requiere `impressions`, `clicks` (Meta Ads API) y
 * `conversations` counts (clasificador conversacional). Cuando alguno de
 * esos está `null`, mostramos un empty card explicativo en lugar de pintar
 * un funnel con ceros engañosos.
 */

import type { ReactElement } from "react";

import {
  totalConversations,
  type AdsCampaign,
} from "@plugins/ads/frontend/entities/ads-campaign";

import { fmtN, fmtPct } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";
import { Icon } from "@/shared/ui";

interface Props {
  campaign: AdsCampaign;
}

interface Stage {
  key: string;
  label: string;
  value: number;
  icon: ReactElement;
  color: string;
}

export function AdsFunnel({ campaign }: Props) {
  const c = campaign;

  // Sin counts conversacionales no hay embudo honesto que pintar (el
  // clasificador es la única fuente irreemplazable). Las impresiones/clics de
  // Meta, en cambio, son opcionales: si faltan arrancamos el embudo en "Chats
  // iniciados" (embudo WhatsApp-only) en vez de ocultarlo entero.
  if (c.conversations === null) {
    return (
      <section className="ads-card">
        <header className="ads-card-h">
          <div>
            <h3>Embudo de conversión</h3>
            <p>Del anuncio en Meta a cliente cerrado en WhatsApp</p>
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
          title="Pendiente: clasificador conversacional"
        >
          <Icon.dataPending />
          <span style={{ fontSize: 13 }}>
            Embudo pendiente — requiere clasificador conversacional
          </span>
        </div>
      </section>
    );
  }

  const won = c.conversations.ganado || 0;
  const totalConvos = totalConversations(c);
  // "En conversación" = todos los que respondieron (engaged) = total − sin
  // respuesta. Incluir ganado/perdido (no solo el pipeline intermedio) mantiene
  // el embudo MONOTÓNICO: cada etapa ⊇ la siguiente. Sino "Cotizados" podía
  // superar a "En conversación" cuando hay más ganados que chats en pipeline,
  // ensanchando una barra a mitad del embudo y mostrando un drop negativo.
  const engaged = totalConvos - (c.conversations.no_reply || 0);

  // Las 2 etapas Meta solo entran si AMBAS métricas existen. Si faltan, el
  // embudo es WhatsApp-only (desde "Chats iniciados").
  const hasMeta = c.impressions !== null && c.clicks !== null;
  const metaStages: Stage[] = hasMeta
    ? [
        { key: "imp", label: "Impresiones",      value: c.impressions as number, icon: <AdsIcon.eye />,   color: "#5fa9ff" },
        { key: "clk", label: "Clics al anuncio", value: c.clicks as number,      icon: <AdsIcon.click />, color: "#5fdcff" },
      ]
    : [];
  const stages: Stage[] = [
    ...metaStages,
    { key: "ini", label: "Chats iniciados",  value: c.started,     icon: <AdsIcon.msg />,    color: "#d68aff" },
    { key: "act", label: "En conversación",  value: engaged,       icon: <AdsIcon.users />,  color: "#ffb44a" },
    { key: "cot", label: "Cotizados",        value: (c.conversations.cotizado || 0) + won, icon: <AdsIcon.dollar />, color: "#ff9f6a" },
    { key: "won", label: "Ganados (cliente)", value: won,          icon: <AdsIcon.trophy />, color: "#5be07b" },
  ];

  const max = stages[0].value || 1;
  // Conversión total: contra impresiones (si hay Meta) o contra chats
  // iniciados (embudo WhatsApp-only).
  const overallConv = max > 0 ? won / max : 0;

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Embudo de conversión</h3>
          <p>
            {hasMeta
              ? "Del anuncio en Meta a cliente cerrado en WhatsApp"
              : "Del chat iniciado a cliente cerrado · impresiones/clics pendientes de Meta Ads"}
          </p>
        </div>
        <span className="ads-card-meta">
          {fmtPct(overallConv, hasMeta ? 3 : 1)}{" "}
          {hasMeta ? "conversión total" : "iniciados → ganados"} ·{" "}
          {fmtN(totalConvos)} chats
        </span>
      </header>
      <div className="funnel">
        {stages.map((s, i) => {
          const w = Math.max(8, (s.value / max) * 100);
          const stepConv = i > 0 && stages[i - 1].value > 0 ? s.value / stages[i - 1].value : 1;
          const cumConv = s.value / max;
          const next = stages[i + 1];
          return (
            <div key={s.key} className="fn-row">
              <div className="fn-l">
                <span
                  className="fn-ico"
                  style={{ color: s.color, background: s.color + "22" }}
                >
                  {s.icon}
                </span>
                <div>
                  <div className="fn-lbl">{s.label}</div>
                  <div className="fn-sub">
                    {fmtPct(cumConv, 2)} del total · paso a paso{" "}
                    {i === 0 ? "—" : fmtPct(stepConv, 1)}
                  </div>
                </div>
              </div>
              <div className="fn-bar-wrap">
                <div
                  className="fn-bar"
                  style={{
                    width: w + "%",
                    background: `linear-gradient(90deg, ${s.color}22, ${s.color}66)`,
                    borderColor: s.color + "55",
                  }}
                >
                  <span className="fn-bar-v">{fmtN(s.value)}</span>
                </div>
              </div>
              {next && (
                <div
                  className="fn-drop"
                  title={`Caen ${fmtN(s.value - next.value)}`}
                >
                  <span>−{fmtN(s.value - next.value)}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
