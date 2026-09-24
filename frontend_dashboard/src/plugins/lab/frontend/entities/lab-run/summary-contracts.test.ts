import { describe, expect, it } from "vitest";

import { armSummarySchema, checkVerdictSchema, runDiffSchema, runReportSchema } from "./contracts";

/** Resumen de una corrida (PR 13): lo que publica la caja, leído tolerante (L-10). */
describe("contratos del Resumen", () => {
  it("sin señal es un veredicto de check (modo turno)", () => {
    expect(checkVerdictSchema.parse("sin_senal")).toBe("sin_senal");
    expect(checkVerdictSchema.parse("raro")).toBe("desconocido");
  });

  it("un brazo trae las gráficas de Calidad LLM y pass^k", () => {
    const s = armSummarySchema.parse({
      reps: 3,
      mode: "turn",
      episodes: 2,
      verdicts: { FALLA: 1, PASA: 1 },
      pareto: [{ check_id: "EST-06", name: "Etapa", level: "mayor", failures: 1 }],
      trend: [],
      funnel: [{ stage: "cierre", FALLA: 1 }],
      pass_k: { k: 3, episodes: 2, rate: 0.5 },
    });
    expect(s.verdicts).toEqual({ FALLA: 1, ALERTA: 0, PASA: 1, SIN_DATOS: 0 });
    expect(s.funnel[0]).toEqual({ stage: "cierre", FALLA: 1, ALERTA: 0, PASA: 0, SIN_DATOS: 0 });
    expect(s.pass_k?.rate).toBe(0.5);
  });

  it("la diferencia trae el intervalo y los turnos que cambiaron", () => {
    const d = runDiffSchema.parse({
      base: "A1",
      cand: "B",
      episode_pass: { delta: 0.1, low: -0.05, high: 0.25, conclusive: false, sessions: 80 },
      pass_k: { base: { k: 3, episodes: 90, rate: 0.3 }, cand: { k: 3, episodes: 90, rate: 0.4 } },
      checks: [{ check_id: "EST-08", delta: 0.2, low: 0.1, high: 0.3, conclusive: true, sessions: 40 }],
      changed_turns: [{ session_id: "wa_573001234567", episode_id: "ep_1", turn: 2, base: "FALLA", cand: "PASA", checks: ["EST-08"] }],
    });
    expect(d.episode_pass.conclusive).toBe(false);
    expect(d.checks[0].conclusive).toBe(true);
    expect(d.changed_turns[0].checks).toEqual(["EST-08"]);
  });

  it("el reporte tolera una corrida sin evaluar todavía", () => {
    const r = runReportSchema.parse({ run_id: "run-x", arms: ["A0"] });
    expect(r.mode).toBe("episode");
    expect(r.fidelity).toBeNull();
    expect(r.arena).toEqual({});
    expect(r.diffs).toEqual([]);
  });

  it("el reporte trae fidelidad, validación y arena", () => {
    const r = runReportSchema.parse({
      mode: "turn",
      arms: ["A0", "A1", "B"],
      fidelity: { n: 120, agreement: 0.94, threshold: 0.9, ok: true },
      validation: { episodes: 91, prod_registry_versions: [3], agreement: 0.97, verdict_agreement: 0.81, checks: {} },
      arena: {
        B: {
          profile: "jev-v1",
          metrics: [{ turns: 300, errors: 2, perception: { turns: 300, fallback_rate: 0.004, p50_ms: 260, p95_ms: 480 },
            verify: null, complement_rate: 0.05, extra_round_rate: 0.02, cost_per_turn_usd: 0.019, perception_cost_per_turn_usd: 0.0002 }],
          topics: { turns: 120, precision: 0.9, recall: 0.8, f1: 0.85, calibration: { n: 400, brier: 0.08, ece: 0.04 } },
        },
      },
      judge: { used: true, errors: 0 },
      diffs: ["A0:A1", "A1:B"],
    });
    expect(r.fidelity?.ok).toBe(true);
    expect(r.validation?.episodes).toBe(91);
    expect(r.arena.B.metrics[0].perception?.p95_ms).toBe(480);
    expect(r.arena.B.topics.calibration.brier).toBe(0.08);
  });
});
