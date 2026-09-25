/**
 * Diagrama de secuencia del hilo de un turno (plan del laboratorio §11.1,
 * diseño aprobado 2026-09-23). Dibuja las filas de `layoutSequence`: flechas
 * entre carriles o cajas dentro del Workflow, con el número del paso, la
 * etiqueta corta y el tiempo desde el inicio del turno.
 *
 * - Escritorio (≥ 760 px): SVG con desplazamiento horizontal propio (ancho
 *   mínimo 560 px). Cada fila es un botón: clic, Enter o espacio la
 *   seleccionan; ↑/↓ recorren los pasos.
 * - Celular (< 760 px): la misma secuencia como lista de botones.
 *
 * Colores: los tokens del tema (`--color-*`) según el estado del paso.
 */

import { useEffect, useRef, type KeyboardEvent } from "react";

import { STEP_COLOR, type SequenceLayout, type StepStatus } from "@/shared/lib";

const CLASSIFIER_LANE = 2;
const NUMBER_INK = "#0b0b0d";

interface Props {
  layout: SequenceLayout;
  selected: number;
  onSelect: (index: number) => void;
  /** Prefijo de ids de los marcadores del SVG (dos diagramas en la misma página). */
  idPrefix?: string;
}

export function SequenceTrace({ layout, selected, onSelect, idPrefix = "seq" }: Props) {
  const { rows, lanes, lanesX, width, height, usesClassifier } = layout;
  const last = rows.length - 1;
  const svgRef = useRef<SVGSVGElement>(null);

  // El foco sigue a la selección solo si ya estaba dentro del diagrama (↑/↓):
  // un clic en la lista del celular o un cambio de brazo no lo roban.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !svg.contains(document.activeElement)) return;
    const target = svg.querySelector<SVGGElement>(`[data-step="${selected}"]`);
    target?.focus();
  }, [selected]);

  const onKey = (index: number) => (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(index);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = selected + (e.key === "ArrowDown" ? 1 : -1);
      if (next >= 0 && next <= last) onSelect(next);
    }
  };

  return (
    <>
      <div className="hidden min-[760px]:block">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label="Secuencia de pasos del turno"
          xmlns="http://www.w3.org/2000/svg"
          className="block h-auto w-full min-w-[560px]"
        >
          <defs>
            {(Object.keys(STEP_COLOR) as StepStatus[]).map((k) => (
              <marker key={k} id={`${idPrefix}-mk-${k}`} markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" style={{ fill: STEP_COLOR[k] }} />
              </marker>
            ))}
          </defs>
          {lanes.map((name, i) => {
            const dim = i === CLASSIFIER_LANE && !usesClassifier;
            const x = lanesX[i];
            return (
              <g key={name + i}>
                <rect x={x - 54} y={8} width={108} height={22} rx={11} style={{ fill: "rgba(255,255,255,0.05)", stroke: "var(--color-line-strong)" }} />
                <text
                  x={x}
                  y={23}
                  textAnchor="middle"
                  style={{
                    fill: dim ? "var(--color-fg-faint)" : i === CLASSIFIER_LANE ? "var(--color-violet)" : "var(--color-fg-soft)",
                    font: "600 11px var(--font-sans)",
                  }}
                >
                  {dim ? `${name} · sin uso` : name}
                </text>
                <line x1={x} y1={32} x2={x} y2={height - 6} strokeDasharray="3,3" style={{ stroke: `rgba(255,255,255,${dim ? 0.05 : 0.12})` }} />
              </g>
            );
          })}
          {rows.map((r) => {
            const on = r.index === selected;
            const c = STEP_COLOR[r.status];
            const n = r.index + 1;
            return (
              <g
                key={r.index}
                data-step={r.index}
                role="button"
                tabIndex={on ? 0 : -1}
                aria-label={`Paso ${n}: ${r.title}`}
                aria-pressed={on}
                onClick={() => onSelect(r.index)}
                onKeyDown={onKey(r.index)}
                className="cursor-pointer outline-none"
              >
                <rect
                  x={4}
                  y={r.y - 20}
                  width={width - 8}
                  height={40}
                  rx={8}
                  style={{ fill: "transparent", stroke: on ? "var(--color-accent)" : "none", strokeWidth: 1 }}
                />
                {r.from === r.to ? <SelfBox x={lanesX[r.from]} y={r.y} color={c} on={on} n={n} label={r.short} /> : <Arrow row={r} lanesX={lanesX} color={c} on={on} n={n} markerId={`${idPrefix}-mk-${r.status}`} />}
                <text x={width - 6} y={r.y + 4} textAnchor="end" style={{ fill: "var(--color-fg-faint)", font: "500 10px var(--font-sans)" }}>
                  {r.t}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <ol aria-label="Pasos del turno" className="grid list-none gap-1.5 p-0 min-[760px]:hidden">
        {rows.map((r) => (
          <li key={r.index}>
            <button
              type="button"
              aria-current={r.index === selected}
              onClick={() => onSelect(r.index)}
              className={
                "grid w-full grid-cols-[26px_1fr_auto] items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-[12.5px] font-medium leading-snug text-fg " +
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
                (r.index === selected ? "border-accent bg-accent-soft" : "border-line bg-white/[0.03]")
              }
            >
              <span className="grid h-[22px] w-[22px] place-items-center rounded-full text-[11px] font-bold" style={{ background: STEP_COLOR[r.status], color: NUMBER_INK }}>
                {r.index + 1}
              </span>
              <span>
                {r.title}
                <small className="block text-[11.5px] font-normal text-fg-muted">
                  {lanes[r.from]} → {lanes[r.to]}
                </small>
              </span>
              <time className="text-[11px] tabular-nums text-fg-faint">{r.t}</time>
            </button>
          </li>
        ))}
      </ol>
    </>
  );
}

function Badge({ x, y, color, ring, n }: { x: number; y: number; color: string; ring: string; n: number }) {
  return (
    <>
      <circle cx={x} cy={y} r={9} style={{ fill: color, stroke: ring, strokeWidth: 2 }} />
      <text x={x} y={y + 3.5} textAnchor="middle" style={{ fill: NUMBER_INK, font: "700 10px var(--font-sans)" }}>
        {n}
      </text>
    </>
  );
}

function SelfBox({ x, y, color, on, n, label }: { x: number; y: number; color: string; on: boolean; n: number; label: string }) {
  return (
    <>
      <rect x={x - 58} y={y - 13} width={116} height={26} rx={6} style={{ fill: "var(--color-toolbar)", stroke: color, strokeWidth: on ? 2 : 1 }} />
      <text x={x} y={y + 4} textAnchor="middle" style={{ fill: "var(--color-fg)", font: "500 10.5px var(--font-sans)" }}>
        {label}
      </text>
      <Badge x={x - 74} y={y} color={color} ring={on ? "#fff" : "none"} n={n} />
    </>
  );
}

function Arrow({
  row,
  lanesX,
  color,
  on,
  n,
  markerId,
}: {
  row: SequenceLayout["rows"][number];
  lanesX: number[];
  color: string;
  on: boolean;
  n: number;
  markerId: string;
}) {
  const dir = row.to > row.from ? 1 : -1;
  const x1 = lanesX[row.from] + dir * 4;
  const x2 = lanesX[row.to] - dir * 6;
  const tw = row.short.length * 5.8 + 10;
  const lx = dir > 0 ? x1 + 28 : x1 - 28 - tw;
  const bx = x1 + dir * 16;
  return (
    <>
      <line
        x1={x1}
        y1={row.y}
        x2={x2}
        y2={row.y}
        strokeDasharray={row.dashed ? "5,4" : undefined}
        markerEnd={`url(#${markerId})`}
        style={{ stroke: color, strokeWidth: on ? 2 : 1.3 }}
      />
      <rect x={lx} y={row.y - 21} width={tw} height={15} rx={3} style={{ fill: "var(--color-canvas)" }} />
      <text x={lx + 5} y={row.y - 10} style={{ fill: on ? "var(--color-fg)" : "var(--color-fg-soft)", font: `${on ? 600 : 500} 10.5px var(--font-sans)` }}>
        {row.short}
      </text>
      <Badge x={bx} y={row.y} color={color} ring={on ? "#fff" : "var(--color-canvas)"} n={n} />
    </>
  );
}
