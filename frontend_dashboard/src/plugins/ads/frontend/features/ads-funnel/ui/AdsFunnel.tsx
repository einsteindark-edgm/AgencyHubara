/**
 * Embudo horizontal de conversión: del impresión Meta al cliente cerrado por
 * WhatsApp. 6 etapas (impresiones → clics → chats iniciados → en conversación
 * → cotizados → ganados), cada una con su barra proporcional, conversion
 * paso-a-paso y drop entre etapas.
 */

import type { ReactElement } from "react";

import {
  totalConversations,
  type AdsCampaign,
} from "@/entities/ads-campaign";

import { fmtN, fmtPct } from "@plugins/ads/frontend/lib/format";
import { AdsIcon } from "@plugins/ads/frontend/lib/icons";

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
  const won = c.conversations.ganado || 0;
  const totalConvos = totalConversations(c);
  const pipeline =
    (c.conversations.nuevo || 0) +
    (c.conversations.activo || 0) +
    (c.conversations.calificado || 0) +
    (c.conversations.cotizado || 0);

  const stages: Stage[] = [
    { key: "imp", label: "Impresiones",      value: c.impressions, icon: <AdsIcon.eye />,    color: "#5fa9ff" },
    { key: "clk", label: "Clics al anuncio", value: c.clicks,      icon: <AdsIcon.click />,  color: "#5fdcff" },
    { key: "ini", label: "Chats iniciados",  value: c.started,     icon: <AdsIcon.msg />,    color: "#d68aff" },
    { key: "act", label: "En conversación",  value: pipeline,      icon: <AdsIcon.users />,  color: "#ffb44a" },
    { key: "cot", label: "Cotizados",        value: (c.conversations.cotizado || 0) + won, icon: <AdsIcon.dollar />, color: "#ff9f6a" },
    { key: "won", label: "Ganados (cliente)", value: won,          icon: <AdsIcon.trophy />, color: "#5be07b" },
  ];

  const max = stages[0].value || 1;
  const overallConv = c.impressions > 0 ? won / c.impressions : 0;

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Embudo de conversión</h3>
          <p>Del anuncio en Meta a cliente cerrado en WhatsApp</p>
        </div>
        <span className="ads-card-meta">
          {fmtPct(overallConv, 3)} conversión total · {fmtN(totalConvos)} chats
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
