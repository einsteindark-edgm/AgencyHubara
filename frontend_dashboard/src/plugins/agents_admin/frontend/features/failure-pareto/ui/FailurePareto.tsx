import { useMemo, type KeyboardEvent } from "react";

import {
  paretoWithCumulative,
  type ParetoItem,
} from "@plugins/agents_admin/frontend/entities/check-stats";
import {
  levelColor,
  levelLabel,
  type CheckLevel,
} from "@plugins/agents_admin/frontend/entities/scorecard";

interface Props {
  pareto: readonly ParetoItem[];
  days: number;
  selectedCheckId?: string | null;
  /** Elegir una barra: la composición filtra la matriz a los episodios que fallan ese check. */
  onSelectCheck: (checkId: string) => void;
}

const W = 560;
const H = 290;
const L = 40;
const R = 46;
const T = 22;
const B = 58;

const LEVELS: CheckLevel[] = ["critico", "mayor", "menor"];

function niceMax(v: number): number {
  if (v <= 4) return 4;
  const step = Math.ceil(v / 4);
  return step * 4;
}

/**
 * Pareto de fallos: una barra por check fallado (coloreada por nivel, con el
 * nivel también en texto) + línea del acumulado. Responde "qué arreglar
 * primero". Click o Enter en una barra elige el check.
 */
export function FailurePareto({ pareto, days, selectedCheckId = null, onSelectCheck }: Props) {
  const rows = useMemo(() => paretoWithCumulative(pareto), [pareto]);

  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
        Ningún check falló en los últimos {days} días.
      </p>
    );
  }

  const maxV = niceMax(rows[0].failures);
  const plotW = W - L - R;
  const plotH = H - T - B;
  const bw = plotW / rows.length;
  const yv = (v: number) => T + plotH * (1 - v / maxV);
  const yp = (p: number) => T + plotH * (1 - p);
  const ticks = [0, maxV / 4, maxV / 2, (3 * maxV) / 4, maxV];
  const points = rows.map((r, i) => [L + i * bw + bw / 2, yp(r.share)] as const);
  // Punto de lectura: el primer check donde el acumulado cruza el 50 %.
  const halfIdx = rows.findIndex((r) => r.share >= 0.5);

  const onKey = (id: string) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelectCheck(id);
    }
  };

  return (
    <figure className="m-0 flex flex-col gap-1">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full max-w-full"
        role="group"
        aria-label={`Pareto de fallos por check en ${days} días`}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={yv(v)} y2={yv(v)} stroke="var(--color-line)" />
            <text x={L - 6} y={yv(v) + 4} textAnchor="end" fontSize={10} fill="var(--color-fg-faint)">
              {Math.round(v)}
            </text>
          </g>
        ))}
        {[0, 0.5, 1].map((p) => (
          <text key={p} x={W - R + 6} y={yp(p) + 4} fontSize={10} fill="var(--color-fg-faint)">
            {Math.round(p * 100)} %
          </text>
        ))}

        {rows.map((r, i) => {
          const x = L + i * bw + 3;
          const w = bw - 6;
          const selected = r.check_id === selectedCheckId;
          const share = Math.round(r.share * 100);
          const label = `${r.check_id}: ${r.failures} fallos · ${levelLabel(r.level)} · ${share} % acumulado`;
          return (
            <g
              key={r.check_id}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={selected}
              onClick={() => onSelectCheck(r.check_id)}
              onKeyDown={onKey(r.check_id)}
              className="group cursor-pointer outline-none"
            >
              <title>{`${label}${r.name ? ` — ${r.name}` : ""}. Elige para ver los episodios.`}</title>
              <rect x={x - 3} y={T} width={bw} height={plotH} fill="transparent" />
              <rect
                x={x}
                y={yv(r.failures)}
                width={w}
                height={yv(0) - yv(r.failures)}
                rx={3}
                fill={levelColor(r.level)}
                stroke={selected ? "var(--color-fg)" : "none"}
                strokeWidth={2}
                className="transition-opacity group-hover:opacity-80 group-focus-visible:opacity-80"
              />
              <text
                x={x + w / 2}
                y={yv(r.failures) - 4}
                textAnchor="middle"
                fontSize={10}
                fontWeight={700}
                fill="var(--color-fg)"
                className={selected ? "" : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"}
              >
                {r.failures}
              </text>
              <text
                x={x + w / 2}
                y={yv(0) + 12}
                fontSize={9.5}
                fontFamily="var(--font-mono)"
                textAnchor="end"
                fill={selected ? "var(--color-fg)" : "var(--color-fg-muted)"}
                transform={`rotate(-45 ${x + w / 2} ${yv(0) + 12})`}
              >
                {r.check_id}
              </text>
            </g>
          );
        })}

        <polyline
          points={points.map((p) => p.join(",")).join(" ")}
          fill="none"
          stroke="var(--color-fg)"
          strokeWidth={1.5}
          pointerEvents="none"
        />
        {points.map(([x, y], i) => (
          <circle key={rows[i].check_id} cx={x} cy={y} r={2.5} fill="var(--color-fg)" pointerEvents="none" />
        ))}
        {halfIdx >= 0 && (
          <text
            x={Math.min(points[halfIdx][0] + 8, W - R - 110)}
            y={points[halfIdx][1] - 8}
            fontSize={11}
            fontWeight={700}
            fill="var(--color-fg)"
            pointerEvents="none"
          >
            {`${Math.round(rows[halfIdx].share * 100)} % en ${halfIdx + 1} check${halfIdx ? "s" : ""}`}
          </text>
        )}
      </svg>
      <ul className="flex flex-wrap items-center gap-3 text-[11px] text-fg-muted" aria-label="Leyenda de niveles">
        {LEVELS.map((l) => (
          <li key={l} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: levelColor(l) }} aria-hidden="true" />
            {levelLabel(l)}
          </li>
        ))}
        <li className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-fg" aria-hidden="true" />
          acumulado
        </li>
      </ul>
    </figure>
  );
}

export default FailurePareto;
