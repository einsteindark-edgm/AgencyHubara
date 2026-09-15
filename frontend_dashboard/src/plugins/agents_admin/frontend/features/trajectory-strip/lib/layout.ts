import {
  STRIP_LANES,
  type CheckStatus,
  type StripCheck,
  type StripColumn,
  type StripLaneId,
  type StripModel,
} from "@plugins/agents_admin/frontend/entities/scorecard";

/** Geometría pura de la tira de trayectoria (px del viewBox SVG). */

export const LABEL_W = 96;
export const COL_W = 132;
export const GAP_W = 56;
export const TOP = 58;
export const BAND_Y = 26;
export const BAND_H = 20;
export const BOTTOM = 28;
export const CHIP_H = 22;
export const CHIP_GAP = 4;
export const LANE_PAD = 8;
export const MIN_LANE_H = 34;
export const DOTS_PER_ROW = 4;
export const DOT_STEP = 30;
/** Punto + id rotado debajo. */
export const DOT_ROW_H = 50;
/** Fila de resumen de checks sin turno (`? 28 sin turno`). */
export const PILL_ROW_H = 26;

export interface LaneBox {
  y: number;
  h: number;
}

export interface StripLayout {
  width: number;
  height: number;
  /** Centro x de cada columna. */
  colX: number[];
  lanes: Record<StripLaneId, LaneBox>;
}

/** Offset del punto `i` de `n` dentro de la columna: filas de `DOTS_PER_ROW` centradas. */
export function dotOffset(i: number, n: number): { dx: number; row: number } {
  const row = Math.floor(i / DOTS_PER_ROW);
  const inRow = Math.min(DOTS_PER_ROW, n - row * DOTS_PER_ROW);
  const pos = i - row * DOTS_PER_ROW;
  return { dx: (pos - (inRow - 1) / 2) * DOT_STEP, row };
}

export interface CollapsedChecks {
  status: CheckStatus;
  checks: StripCheck[];
}

/**
 * Qué checks de una columna se dibujan como punto: las fallas, los anclados a
 * un evento del turno y el seleccionado. Los sin turno que no fallan (típico:
 * decenas de `desconocido`/`pasa` que caen al último turno) se resumen por
 * estado para que la tira siga legible; el panel los lista uno a uno.
 */
export function splitColumnChecks(
  column: StripColumn,
  selectedCheckId: string | null,
): { dots: StripCheck[]; collapsed: CollapsedChecks[] } {
  const dots: StripCheck[] = [];
  const groups = new Map<CheckStatus, StripCheck[]>();
  for (const c of column.checks) {
    if (c.anchored || c.verdict === "falla" || c.checkId === selectedCheckId) {
      dots.push(c);
    } else {
      const list = groups.get(c.status) ?? [];
      list.push(c);
      groups.set(c.status, list);
    }
  }
  return { dots, collapsed: [...groups].map(([status, checks]) => ({ status, checks })) };
}

export function stripLayout(model: StripModel, selectedCheckId: string | null = null): StripLayout {
  const colX: number[] = [];
  let x = LABEL_W + COL_W / 2;
  model.columns.forEach((col, i) => {
    if (i > 0) x += COL_W + (col.gapBeforeMs !== null ? GAP_W : 0);
    colX.push(x);
  });

  const laneHeight = (id: StripLaneId): number => {
    if (id === "checks") {
      const h = Math.max(
        0,
        ...model.columns.map((c) => {
          const { dots, collapsed } = splitColumnChecks(c, selectedCheckId);
          return (
            Math.ceil(dots.length / DOTS_PER_ROW) * DOT_ROW_H + (collapsed.length ? PILL_ROW_H : 0)
          );
        }),
      );
      return Math.max(MIN_LANE_H, h + LANE_PAD * 2);
    }
    const n = Math.max(0, ...model.columns.map((c) => c.lanes[id].length));
    return Math.max(MIN_LANE_H, n * (CHIP_H + CHIP_GAP) - CHIP_GAP + LANE_PAD * 2);
  };

  const lanes = {} as Record<StripLaneId, LaneBox>;
  let y = TOP;
  for (const lane of STRIP_LANES) {
    const h = laneHeight(lane.id);
    lanes[lane.id] = { y, h };
    y += h;
  }

  const lastX = colX.at(-1) ?? LABEL_W;
  return {
    width: Math.max(LABEL_W + COL_W, lastX + COL_W / 2 + 12),
    height: y + BOTTOM,
    colX,
    lanes,
  };
}
