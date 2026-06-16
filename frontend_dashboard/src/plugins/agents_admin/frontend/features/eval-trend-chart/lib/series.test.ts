import { describe, expect, it } from "vitest";

import type { ConversationEval } from "@plugins/agents_admin/frontend/entities/episode-eval";
import type { EvalTrendSeries } from "@plugins/agents_admin/frontend/entities/eval-trend";

import {
  fmtDate,
  fmtTime,
  lineFromAggregate,
  linesFromClientEpisodes,
  linesFromEpisode,
} from "./series";

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

describe("linesFromClientEpisodes", () => {
  // Tres episodios del mismo cliente, cronológico (viejo → nuevo). Cada uno con
  // UNA eval (como en prod: event-driven al cerrar). greeting empeora 1→0; el
  // no_hallucination mejora 0→1.
  const mk = (
    episode_id: string,
    last_date: string,
    metrics: Record<string, number>,
    failed: string[],
  ) =>
    ({
      session_id: "wa_cliente",
      episode_id,
      last_date,
      last_ts: `${last_date}T10:00:00+00:00`,
      last_avg: 0.5,
      closing_tag: null,
      evals: [
        {
          date: last_date,
          ts: `${last_date}T10:00:00+00:00`,
          avg: 0.5,
          passed: false,
          is_candidate: false,
          metrics,
          failed: failed.map((m) => ({ metric: m, score: metrics[m], reason: "x" })),
        },
      ],
    }) as unknown as ConversationEval;

  const episodes = [
    mk("ep_001", "2026-06-01", { greeting: 1, no_hallucination: 0 }, ["no_hallucination"]),
    mk("ep_002", "2026-06-02", { greeting: 0.5, no_hallucination: 0.5 }, ["no_hallucination"]),
    mk("ep_003", "2026-06-03", { greeting: 0, no_hallucination: 1 }, ["greeting"]),
  ];

  it("un punto por episodio (no por re-eval), valor = última eval del episodio", () => {
    const lines = linesFromClientEpisodes(episodes);
    const byMetric = Object.fromEntries(lines.map((l) => [l.metric, l]));
    expect(byMetric.greeting.points.map((p) => p.value)).toEqual([1, 0.5, 0]);
    expect(byMetric.no_hallucination.points.map((p) => p.value)).toEqual([0, 0.5, 1]);
    // la etiqueta del punto es el episodio (no la fecha)
    expect(byMetric.greeting.points[0].label).toBe("ep_001");
  });

  it("below sale del failed POR métrica de cada episodio", () => {
    const lines = linesFromClientEpisodes(episodes);
    const byMetric = Object.fromEntries(lines.map((l) => [l.metric, l]));
    // no_hallucination falló en ep_001 y ep_002, no en ep_003
    expect(byMetric.no_hallucination.points.map((p) => p.below)).toEqual([true, true, false]);
    // greeting solo falló en ep_003
    expect(byMetric.greeting.points.map((p) => p.below)).toEqual([false, false, true]);
  });

  it("un solo episodio en el rango → inicio == actual (un punto)", () => {
    const lines = linesFromClientEpisodes([episodes[0]]);
    expect(lines[0].points).toHaveLength(1);
  });
});
