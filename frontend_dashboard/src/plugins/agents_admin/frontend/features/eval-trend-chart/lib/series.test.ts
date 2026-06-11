import { describe, expect, it } from "vitest";

import type { ConversationEval } from "@plugins/agents_admin/frontend/entities/episode-eval";
import type { EvalTrendSeries } from "@plugins/agents_admin/frontend/entities/eval-trend";

import { fmtDate, fmtTime, lineFromAggregate, linesFromEpisode } from "./series";

describe("fmtDate / fmtTime", () => {
  it("formatea fecha es-CO y hora", () => {
    expect(fmtDate("2026-06-01")).toBe("1 jun");
    expect(fmtDate("2026-12-25")).toBe("25 dic");
    expect(fmtDate("no-es-fecha")).toBe("no-es-fecha"); // tolerante
    expect(fmtTime("2026-06-01T14:30:00+00:00")).toBe("14:30");
    expect(fmtTime("")).toBe("");
  });
});

describe("lineFromAggregate", () => {
  it("mapea puntos por día y marca los que están bajo el umbral", () => {
    const series: EvalTrendSeries = {
      metric: "greeting",
      points: [
        { date: "2026-06-01", avg: 0.4, min: 0.2, n: 3, n_below: 2 },
        { date: "2026-06-02", avg: 0.9, min: 0.8, n: 1, n_below: 0 },
      ],
    };
    const line = lineFromAggregate(series, 0.7);
    expect(line.metric).toBe("greeting");
    expect(line.points[0].below).toBe(true);
    expect(line.points[1].below).toBe(false);
    expect(line.points[0].label).toBe("1 jun"); // valor inicial con su fecha
    expect(line.points.at(-1)!.label).toBe("2 jun"); // valor actual con su fecha
  });
});

describe("linesFromEpisode", () => {
  const conv = {
    session_id: "wa_x",
    episode_id: "ep_002",
    evals: [
      {
        date: "2026-06-01",
        ts: "2026-06-01T08:00:00+00:00",
        avg: 0.5,
        passed: false,
        is_candidate: true,
        metrics: { greeting: 0.0, style: 1.0 },
        failed: [{ metric: "greeting", score: 0.0, reason: "no saludó" }],
      },
      {
        date: "2026-06-02",
        ts: "2026-06-02T08:00:00+00:00",
        avg: 0.9,
        passed: true,
        is_candidate: false,
        metrics: { greeting: 0.8, style: 1.0 },
        failed: [],
      },
    ],
  } as unknown as ConversationEval;

  it("pivotea las métricas en series por-métrica en orden cronológico", () => {
    const lines = linesFromEpisode(conv);
    const byMetric = Object.fromEntries(lines.map((l) => [l.metric, l]));
    expect(Object.keys(byMetric).sort()).toEqual(["greeting", "style"]);
    // greeting evoluciona 0.0 -> 0.8 (avance visible)
    expect(byMetric.greeting.points.map((p) => p.value)).toEqual([0.0, 0.8]);
    // below sale del `failed` de cada eval, no de un umbral global
    expect(byMetric.greeting.points[0].below).toBe(true);
    expect(byMetric.greeting.points[1].below).toBe(false);
    expect(byMetric.style.points.every((p) => !p.below)).toBe(true);
    // etiqueta del punto inicial incluye fecha + hora
    expect(byMetric.greeting.points[0].label).toBe("1 jun 08:00");
  });

  it("tolera un episodio con una sola eval (sin evolución todavía)", () => {
    const single = { ...conv, evals: [conv.evals[0]] } as ConversationEval;
    const lines = linesFromEpisode(single);
    expect(lines[0].points).toHaveLength(1);
  });
});
