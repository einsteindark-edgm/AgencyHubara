import { useMemo, type KeyboardEvent } from "react";

import {
  STRIP_LANES,
  buildStripModel,
  formatDuration,
  stageColor,
  statusColor,
  statusGlyph,
  statusLabel,
  triggerLabel,
  truncate,
  type CheckRegistry,
  type CheckResult,
  type CheckStatus,
  type StripChip,
  type StripChipKind,
  type Trajectory,
} from "@plugins/agents_admin/frontend/entities/scorecard";

import {
  BAND_H,
  BAND_Y,
  CHIP_GAP,
  CHIP_H,
  COL_W,
  DOT_ROW_H,
  DOTS_PER_ROW,
  GAP_W,
  LANE_PAD,
  PILL_ROW_H,
  TOP,
  dotOffset,
  splitColumnChecks,
  stripLayout,
} from "../lib/layout";

interface Props {
  trajectory: Trajectory;
  results: readonly CheckResult[];
  /** Aporta nombres de checks y la alerta del carril de estado. */
  registry?: CheckRegistry;
  selectedCheckId: string | null;
  onSelectCheck: (checkId: string) => void;
}

/** Aproximación de ancho de carácter a 11px (sans) para truncar chips. */
const CHAR_W = 6.1;
const CHIP_MAX_CHARS = Math.floor((COL_W - 16) / CHAR_W);

interface ChipStyle {
  fill: string;
  stroke: string;
  text: string;
  dashed?: boolean;
  prefix?: string;
}

const CHIP_STYLES: Record<StripChipKind, ChipStyle> = {
  customer: { fill: "var(--color-bubble-out)", stroke: "var(--color-line-strong)", text: "var(--color-fg)" },
  handoff: { fill: "var(--color-bubble-out)", stroke: "var(--color-line-strong)", text: "var(--color-fg-soft)", prefix: "↳ " },
  system: { fill: "none", stroke: "var(--color-line-strong)", text: "var(--color-fg-faint)", dashed: true },
  signal: { fill: "var(--color-cyan-soft)", stroke: "var(--color-cyan)", text: "var(--color-cyan)" },
  sent: { fill: "var(--color-accent-soft)", stroke: "var(--color-accent)", text: "var(--color-fg)" },
  suppressed: { fill: "none", stroke: "var(--color-fg-faint)", text: "var(--color-fg-muted)", dashed: true, prefix: "⊘ " },
  discarded: { fill: "none", stroke: "var(--color-fg-faint)", text: "var(--color-fg-muted)", dashed: true, prefix: "⊘ " },
  tool_ok: { fill: "var(--color-neutral-soft)", stroke: "var(--color-line-strong)", text: "var(--color-fg)" },
  tool_rejected: { fill: "var(--color-danger-soft)", stroke: "var(--color-red)", text: "var(--color-red)", dashed: true, prefix: "✗ " },
  tool_unknown: { fill: "var(--color-neutral-soft)", stroke: "var(--color-fg-faint)", text: "var(--color-fg-soft)", dashed: true },
  intent: { fill: "var(--color-info-soft)", stroke: "var(--color-info)", text: "var(--color-fg)", prefix: "▤ " },
  tag: { fill: "var(--color-cyan-soft)", stroke: "var(--color-cyan)", text: "var(--color-fg)" },
  route: { fill: "var(--color-neutral-soft)", stroke: "var(--color-neutral)", text: "var(--color-fg)" },
  confirmed: { fill: "var(--color-ok-soft)", stroke: "var(--color-ok)", text: "var(--color-fg)", prefix: "✓ " },
  guard: { fill: "none", stroke: "var(--color-fg-faint)", text: "var(--color-fg-soft)", prefix: "☑ " },
};

const ALERT_STYLE: ChipStyle = {
  fill: "var(--color-danger-soft)",
  stroke: "var(--color-red)",
  text: "var(--color-fg)",
  prefix: "⚠ ",
};

function Chip({ cx, cy, chip }: { cx: number; cy: number; chip: StripChip }) {
  const style = chip.alert ? { ...CHIP_STYLES[chip.kind], ...ALERT_STYLE } : CHIP_STYLES[chip.kind];
  const label = truncate((style.prefix ?? "") + chip.text, CHIP_MAX_CHARS);
  const w = Math.min(COL_W - 8, Math.max(40, label.length * CHAR_W + 14));
  return (
    <g>
      <title>{chip.alert ? `⚠ Señalado por un check de estado · ${chip.detail}` : chip.detail}</title>
      <rect
        x={cx - w / 2}
        y={cy - CHIP_H / 2}
        width={w}
        height={CHIP_H}
        rx={5}
        fill={style.fill}
        stroke={style.stroke}
        strokeWidth={1}
        strokeDasharray={style.dashed ? "3 2" : undefined}
      />
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize={11} fill={style.text}>
        {label}
      </text>
    </g>
  );
}

function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

/** Leyenda de estados de check: glifo + texto, nunca solo color. */
const LEGEND_STATUSES: CheckStatus[] = ["pasa", "menor", "mayor", "critico", "desconocido", "no_aplica"];

/**
 * Tira de trayectoria de un episodio: diagrama de secuencia en SVG con una
 * columna por turno, bandas de etapa rotuladas, carriles (cliente, bot, tools,
 * componentes, estado, guardas) y los checks anclados al turno que juzgan. Lo
 * que se ve es exactamente lo que evaluó el scorecard (misma `Trayectoria`).
 */
export function TrajectoryStrip({ trajectory, results, registry, selectedCheckId, onSelectCheck }: Props) {
  const model = useMemo(
    () => buildStripModel(trajectory, results, registry),
    [trajectory, results, registry],
  );
  const layout = useMemo(() => stripLayout(model, selectedCheckId), [model, selectedCheckId]);

  if (model.columns.length === 0) {
    return (
      <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
        Sin turnos registrados en la trayectoria de este episodio.
      </p>
    );
  }

  const { width, height, colX, lanes } = layout;
  const bodyBottom = height - 22;
  const stagesPresent = [...new Map(model.bands.map((b) => [b.stage, b.label])).entries()];

  return (
    <figure className="m-0 flex min-w-0 flex-col gap-2">
      <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-muted" aria-label="Leyenda de la tira">
        {stagesPresent.map(([stage, label]) => (
          <li key={stage ?? "sin-etapa"} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: stageColor(stage) }} aria-hidden="true" />
            {label}
          </li>
        ))}
        <li className="mx-1 h-3 w-px bg-line" aria-hidden="true" />
        {LEGEND_STATUSES.map((s) => (
          <li key={s} className="inline-flex items-center gap-1">
            <span
              className="inline-grid h-3.5 w-3.5 place-items-center rounded-full text-[9px] font-bold text-win-bg"
              style={{ background: statusColor(s) }}
              aria-hidden="true"
            >
              {statusGlyph(s)}
            </span>
            {statusLabel(s)}
          </li>
        ))}
      </ul>

      <div className="overflow-x-auto rounded-lg border border-line bg-white/[0.02]">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label={`Tira de trayectoria: ${model.columns.length} turnos, etapas ${model.bands.map((b) => b.label).join(", ")}`}
          className="block"
        >
          {/* Bandas de etapa: rótulo visible + fondo tenue por columna. */}
          {model.bands.map((band) => {
            const x0 = colX[band.from] - COL_W / 2 + 4;
            const x1 = colX[band.to] + COL_W / 2 - 4;
            const color = stageColor(band.stage);
            return (
              <g key={`${band.from}-${band.stage}`}>
                <title>{`Etapa ${band.label}: turnos ${model.columns[band.from].turn}–${model.columns[band.to].turn}`}</title>
                <rect x={x0} y={TOP - 4} width={x1 - x0} height={bodyBottom - TOP + 4} fill={color} opacity={0.06} />
                <rect x={x0} y={BAND_Y} width={x1 - x0} height={BAND_H} rx={4} fill={color} />
                <text x={x0 + 8} y={BAND_Y + 14} fontSize={11} fontWeight={700} fill="var(--color-win-bg)">
                  {band.label}
                </text>
              </g>
            );
          })}

          {/* Carriles */}
          {STRIP_LANES.map((lane) => {
            const box = lanes[lane.id];
            return (
              <g key={lane.id}>
                <line x1={8} x2={width - 8} y1={box.y + box.h} y2={box.y + box.h} stroke="var(--color-line)" />
                <text
                  x={10}
                  y={box.y + Math.min(box.h / 2, 22) + 4}
                  fontSize={10}
                  fontWeight={600}
                  letterSpacing="0.06em"
                  fill="var(--color-fg-faint)"
                  style={{ textTransform: "uppercase" }}
                >
                  {lane.label}
                </text>
              </g>
            );
          })}

          {model.columns.map((col, i) => {
            const cx = colX[i];
            const edgeX = cx - COL_W / 2 + 4;
            const { dots, collapsed } = splitColumnChecks(col, selectedCheckId);
            const checksBox = lanes.checks;
            const markers = [col.isFirstFailure && "primer fallo", col.isFirstCritical && "primer crítico"].filter(Boolean);
            return (
              <g key={col.turn}>
                {col.gapBeforeMs !== null && (
                  <g>
                    <title>{`Hueco de ${formatDuration(col.gapBeforeMs)} entre turnos`}</title>
                    <line
                      x1={cx - COL_W / 2 - GAP_W / 2}
                      x2={cx - COL_W / 2 - GAP_W / 2}
                      y1={TOP}
                      y2={bodyBottom}
                      stroke="var(--color-line-strong)"
                      strokeDasharray="2 4"
                    />
                    <text
                      x={cx - COL_W / 2 - GAP_W / 2}
                      y={TOP - 6}
                      textAnchor="middle"
                      fontSize={10}
                      fill="var(--color-fg-muted)"
                    >
                      {`⏸ ${formatDuration(col.gapBeforeMs)}`}
                    </text>
                  </g>
                )}

                {markers.length > 0 && (
                  <g>
                    <line x1={edgeX} x2={edgeX} y1={16} y2={bodyBottom} stroke="var(--color-red)" strokeWidth={1.5} strokeDasharray="4 3" />
                    <text x={edgeX + 4} y={14} fontSize={11} fontWeight={700} fill="var(--color-red)">
                      {markers.join(" · ")}
                    </text>
                  </g>
                )}

                {(["cliente", "bot", "tools", "componentes", "estado", "guardas"] as const).map((laneId) => {
                  const box = lanes[laneId];
                  const chips = col.lanes[laneId];
                  if (laneId === "bot" && chips.length === 0 && col.trigger === "ghost") {
                    return (
                      <text key={laneId} x={cx} y={box.y + LANE_PAD + CHIP_H / 2 + 4} textAnchor="middle" fontSize={10} fill="var(--color-fg-faint)">
                        sin texto
                      </text>
                    );
                  }
                  return chips.map((chip, k) => (
                    <Chip
                      key={`${laneId}-${k}`}
                      cx={cx}
                      cy={box.y + LANE_PAD + CHIP_H / 2 + k * (CHIP_H + CHIP_GAP)}
                      chip={chip}
                    />
                  ));
                })}

                {dots.map((c, k) => {
                  const { dx, row } = dotOffset(k, dots.length);
                  const px = cx + dx;
                  const py = checksBox.y + LANE_PAD + 10 + row * DOT_ROW_H;
                  const selected = c.checkId === selectedCheckId;
                  const where = c.anchored ? `turno ${col.turn}` : "sin turno";
                  return (
                    <g
                      key={c.checkId}
                      role="button"
                      tabIndex={0}
                      aria-pressed={selected}
                      aria-label={`${c.checkId} · ${c.name} · ${statusLabel(c.status)} · ${where}`}
                      onClick={() => onSelectCheck(c.checkId)}
                      onKeyDown={onActivate(() => onSelectCheck(c.checkId))}
                      className="cursor-pointer outline-none [&:focus-visible>circle]:stroke-accent-fg"
                    >
                      <title>{`${c.checkId} · ${c.name} · ${statusLabel(c.status)} · ${where}`}</title>
                      <circle
                        cx={px}
                        cy={py}
                        r={selected ? 10.5 : 9}
                        fill={statusColor(c.status)}
                        stroke={selected ? "var(--color-fg)" : "var(--color-canvas)"}
                        strokeWidth={selected ? 2.5 : 2}
                      />
                      <text x={px} y={py + 4} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--color-win-bg)">
                        {statusGlyph(c.status)}
                      </text>
                      <text
                        x={px}
                        y={py + 22}
                        fontSize={9}
                        fontFamily="var(--font-mono)"
                        fill={selected ? "var(--color-fg)" : "var(--color-fg-muted)"}
                        textAnchor="end"
                        transform={`rotate(-38 ${px} ${py + 22})`}
                      >
                        {c.checkId}
                      </text>
                    </g>
                  );
                })}

                {collapsed.map((g, k) => {
                  const rows = Math.ceil(dots.length / DOTS_PER_ROW);
                  const py = checksBox.y + LANE_PAD + rows * DOT_ROW_H + PILL_ROW_H / 2;
                  const pillW = (COL_W - 12) / collapsed.length;
                  const px = cx - (COL_W - 12) / 2 + pillW * k + pillW / 2;
                  return (
                    <g key={g.status}>
                      <title>{`Sin turno · ${statusLabel(g.status)} (${g.checks.length}): ${g.checks.map((c) => c.checkId).join(", ")}`}</title>
                      <rect x={px - pillW / 2 + 2} y={py - 9} width={pillW - 4} height={18} rx={9} fill="none" stroke={statusColor(g.status)} />
                      <text x={px} y={py + 4} textAnchor="middle" fontSize={10} fill="var(--color-fg-soft)">
                        {`${statusGlyph(g.status)} ${g.checks.length}`}
                      </text>
                    </g>
                  );
                })}

                <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--color-fg-faint)">
                  {`turno ${col.turn}`}
                  {col.trigger !== "customer" ? ` · ${triggerLabel(col.trigger)}` : ""}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="text-[11px] text-fg-faint">
        Pasa el cursor por un chip para ver el texto completo. Los checks sin turno se anclan al último turno;
        los que no fallan se resumen por estado (su detalle está en el scorecard).
      </figcaption>
    </figure>
  );
}

export default TrajectoryStrip;
