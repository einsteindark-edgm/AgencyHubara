import { describe, expect, it } from "vitest";

import threadFixture from "./fixtures/thread.json";
import {
  activeRunSchema,
  benchReportSchema,
  conversationsSchema,
  estimateSchema,
  evaluationsSchema,
  runsSchema,
  threadSchema,
  turnTraceSchema,
} from "./contracts";

/**
 * Contratos del laboratorio (`/api/lab/*` → `lab@v1` de chats). Tolerantes
 * (L-10): un campo que falta o un valor nuevo degrada a un neutro en vez de
 * vaciar la sección entera.
 */

describe("contratos del laboratorio", () => {
  it("la lista de corridas tolera campos ausentes y fases nuevas", () => {
    const parsed = runsSchema.parse({
      runs: [
        { run_id: "run-20260923-1041-ab12", arms: ["A0", "A1"], phase: "simulating", counts: { cases: 309 } },
        { run_id: "run-20260920-0900-cd34" },
      ],
    });

    expect(parsed.runs[0]).toMatchObject({ run_id: "run-20260923-1041-ab12", phase: "simulating", arms: ["A0", "A1"] });
    expect(parsed.runs[1]).toMatchObject({ arms: [], notes: [], phase: null, spent_usd: null });
  });

  it("el estimado trae costo, topes y si cabe", () => {
    const parsed = estimateSchema.parse({
      bench_id: null,
      turns: 309,
      arms: [{ id: "A1", label: "Bot actual (control)", selected: true }],
      reps: 1,
      estimate_usd: 3.2,
      run_cap_usd: 120,
      month_cap_usd: 300,
      month_spent_usd: 10,
      month_left_usd: 290,
      fits: true,
      reason: null,
      spend_limit_usd: 120,
    });

    expect(parsed.fits).toBe(true);
    expect(parsed.estimate_usd).toBe(3.2);
  });

  it("la corrida activa puede no existir", () => {
    expect(activeRunSchema.parse({ active: null }).active).toBeNull();
    const running = activeRunSchema.parse({ active: { phase: "running", run_id: "run-20260923-1041-ab12", turns_done: 10, turns_total: 309 } });
    expect(running.active?.turns_done).toBe(10);
  });

  it("el banco trae conteos y cada exclusión con su motivo", () => {
    const parsed = benchReportSchema.parse({
      bench_id: "bench-run-x",
      counts: { sessions: 82, cases: 309, excluded_turns: 95 },
      exclusions: [{ id: "wa_573001234567/ep_1/t3", reason: "human_intervention" }],
    });

    expect(parsed.counts.cases).toBe(309);
    expect(parsed.exclusions[0].reason).toBe("human_intervention");
  });

  it("el índice de conversaciones trae el veredicto por brazo y episodio; uno desconocido es SIN_DATOS", () => {
    const parsed = conversationsSchema.parse({
      conversations: [
        { session_id: "wa_573001234567", turns: 4, episodes: ["ep_1"], last_at_ms: 1790182869000, verdicts: { A0: { ep_1: "ALERTA", ep_2: "RARO" } } },
      ],
    });

    expect(parsed.conversations[0].verdicts.A0).toEqual({ ep_1: "ALERTA", ep_2: "SIN_DATOS" });
  });

  it("el hilo trae mensajes, episodios y por turno la ráfaga y la salida de cada brazo", () => {
    const parsed = threadSchema.parse(threadFixture);

    expect(parsed.turns[1].burst).toHaveLength(2);
    expect(parsed.turns[1].outputs.A0?.discarded_narration).toEqual(["¡Claro! te comparto el catálogo"]);
    expect(parsed.messages[5]).toMatchObject({ role: "user", has_image: true });
  });

  it("la traza de un turno conserva los campos propios de cada paso", () => {
    const parsed = turnTraceSchema.parse({
      fidelity: "v2",
      arm: "A0",
      rep: 0,
      trace: { turn: 2 },
      steps: [
        { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "hola" }] },
        { i: 1, at_ms: 10, kind: "tool", name: "search_products", ok: true, excerpt: "12 productos" },
      ],
    });

    expect(parsed.steps[1]).toMatchObject({ kind: "tool", name: "search_products", excerpt: "12 productos" });
  });

  it("una traza v1 sin tiempos sigue siendo válida", () => {
    const parsed = turnTraceSchema.parse({ fidelity: "v1", arm: "A0", rep: 0, trace: {}, steps: [{ i: 1, kind: "guard", name: "x" }] });

    expect(parsed.steps[0].at_ms).toBeNull();
    expect(parsed.fidelity).toBe("v1");
  });

  it("las evaluaciones de un brazo traen cada check con su veredicto", () => {
    const parsed = evaluationsSchema.parse({
      arm: "A0",
      rep: 0,
      episodes: [
        {
          session_id: "wa_573001234567",
          episode_id: "ep_1",
          verdict: "ALERTA",
          results: [
            { check_id: "EST-06", verdict: "falla", turn: 2, evidence: "texto descartado", critique: null, source: "code" },
            { check_id: "EST-08", verdict: "falla", turn: 2, topics: [{ topic: "catálogo", turn: 2, msg: 1, covered: false, evidence: "" }] },
          ],
        },
      ],
    });

    expect(parsed.episodes[0].results[0]).toMatchObject({ check_id: "EST-06", verdict: "falla", turn: 2, topics: [] });
    expect(parsed.episodes[0].results[1].topics[0]).toMatchObject({ topic: "catálogo", msg: 1, covered: false });
  });
});
