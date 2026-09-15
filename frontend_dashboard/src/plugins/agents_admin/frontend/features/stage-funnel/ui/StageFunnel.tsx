import { useMemo } from "react";

import {
  funnelTotal,
  sortFunnel,
  type FunnelRow,
} from "@plugins/agents_admin/frontend/entities/check-stats";
import {
  EPISODE_VERDICT_ORDER,
  episodeVerdictColor,
  episodeVerdictLabel,
  stageColor,
  stageLabel,
} from "@plugins/agents_admin/frontend/entities/scorecard";

interface Props {
  funnel: readonly FunnelRow[];
}

const W = 560;
const L = 128;
const R = 36;
const T = 8;
const ROW_H = 34;
const AXIS_H = 22;

/**
 * Embudo de etapa terminal: dónde terminan los episodios y con qué veredicto.
 * Barras horizontales apiladas (Falla → Alerta → Pasa → Sin datos) con total
 * por etapa; el conteo de cada tramo va en su tooltip y, si cabe, adentro.
 */
export function StageFunnel({ funnel }: Props) {
  const rows = useMemo(() => sortFunnel(funnel), [funnel]);

  if (rows.length === 0) {
    return <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">Sin episodios en la ventana.</p>;
  }

  const maxN = Math.max(1, ...rows.map(funnelTotal));
  const H = T + rows.length * ROW_H + AXIS_H;
  const plotW = W - L - R;
  const xv = (v: number) => L + (plotW * v) / maxN;
  const summary = rows.map((r) => `${stageLabel(r.stage)} ${funnelTotal(r)}`).join(", ");

  return (
    <figure className="m-0 flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Embudo de etapa terminal: ${summary}`}>
        {[0, 0.5, 1].map((p) => (
          <g key={p}>
            <line x1={xv(maxN * p)} x2={xv(maxN * p)} y1={T} y2={H - AXIS_H} stroke="var(--color-line)" />
            <text x={xv(maxN * p)} y={H - 6} textAnchor="middle" fontSize={10} fill="var(--color-fg-faint)">
              {Math.round(maxN * p)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const y = T + i * ROW_H + 6;
          const h = ROW_H - 12;
          const total = funnelTotal(r);
          let x = L;
          return (
            <g key={r.stage}>
              <rect x={8} y={y + h / 2 - 5} width={10} height={10} rx={2} fill={stageColor(r.stage)} />
              <text data-testid="funnel-stage" x={24} y={y + h / 2 + 4} fontSize={12} fill="var(--color-fg)">
                {stageLabel(r.stage)}
              </text>
              {EPISODE_VERDICT_ORDER.map((v) => {
                const n = r[v];
                if (!n) return null;
                const w = xv(n) - L;
                const segX = x;
                x += w;
                return (
                  <g key={v}>
                    <title>{`${stageLabel(r.stage)} · ${episodeVerdictLabel(v)}: ${n}`}</title>
                    <rect x={segX} y={y} width={Math.max(0, w - 2)} height={h} rx={3} fill={episodeVerdictColor(v)} />
                    {w > 20 && (
                      <text x={segX + (w - 2) / 2} y={y + h / 2 + 4} textAnchor="middle" fontSize={10} fontWeight={700} fill="var(--color-win-bg)">
                        {n}
                      </text>
                    )}
                  </g>
                );
              })}
              <text data-testid="funnel-total" x={x + 6} y={y + h / 2 + 4} fontSize={11} fill="var(--color-fg-muted)">
                {total}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="flex flex-wrap items-center gap-3 text-[11px] text-fg-muted" aria-label="Leyenda de veredictos">
        {EPISODE_VERDICT_ORDER.map((v) => (
          <li key={v} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: episodeVerdictColor(v) }} aria-hidden="true" />
            {episodeVerdictLabel(v)}
          </li>
        ))}
      </ul>
    </figure>
  );
}

export default StageFunnel;
