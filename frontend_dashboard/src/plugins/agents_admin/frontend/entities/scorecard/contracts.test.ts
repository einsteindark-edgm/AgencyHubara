import { describe, expect, it } from "vitest";

import checksFixture from "./fixtures/checks.json";
import detailFixture from "./fixtures/scorecard-detail.json";
import listFixture from "./fixtures/scorecards.json";
import {
  checkRegistrySchema,
  scorecardDetailSchema,
  scorecardListSchema,
} from "./contracts";

/**
 * Contrato del boundary de `/api/agents/evals/{checks,scorecards,scorecard}`.
 * Los fixtures `checks.json` y `scorecard-detail.json` son payloads REALES del
 * backend (el incidente de PR #281); `scorecards.json` sigue el mismo contrato.
 */
describe("checkRegistrySchema", () => {
  it("parsea el registro real con etapas, familias y checks", () => {
    const reg = checkRegistrySchema.parse(checksFixture);
    expect(reg.registry_version).toBe(1);
    expect(reg.stages).toContain("datos_envio");
    expect(reg.families.find((f) => f.id === "estado")?.stage).toBe("transversal");
    const con01 = reg.checks.find((c) => c.id === "CON-01");
    expect(con01?.level).toBe("critico");
    expect(con01?.kind).toBe("code");
    expect(reg.checks.find((c) => c.id === "TAG-01b")?.twin_of).toBe("TAG-01");
  });

  it("degrada niveles y tipos desconocidos sin vaciar el registro", () => {
    const reg = checkRegistrySchema.parse({
      checks: [{ id: "X-01", name: "Nuevo", family: "f", level: "gravisimo", kind: "llm" }],
    });
    expect(reg.checks[0].level).toBe("menor");
    expect(reg.checks[0].kind).toBe("code");
    expect(reg.checks[0].origin).toEqual([]);
    expect(reg.checks[0].twin_of).toBeNull();
  });
});

describe("scorecardListSchema", () => {
  it("parsea la lista con veredictos, conteos y resultados por check", () => {
    const list = scorecardListSchema.parse(listFixture);
    expect(list.scorecards).toHaveLength(7);
    const incident = list.scorecards[0];
    expect(incident.verdict).toBe("FALLA");
    expect(incident.counts.critico).toBe(5);
    expect(incident.first_critical).toEqual({ turn: 5, check_id: "VAR-01" });
    expect(incident.checks["CON-01"]).toBe("falla");
    const empty = list.scorecards.at(-1)!;
    expect(empty.verdict).toBe("SIN_DATOS");
    expect(empty.compliance).toBeNull();
    expect(empty.first_failure).toBeNull();
  });

  it("tolera veredictos nuevos del backend", () => {
    const list = scorecardListSchema.parse({
      scorecards: [
        {
          session_id: "wa_570000000009",
          episode_id: "ep_001",
          verdict: "REVISAR",
          fidelity: "parcial",
          checks: { "APE-01": "tal_vez" },
        },
      ],
    });
    expect(list.scorecards[0].verdict).toBe("SIN_DATOS");
    expect(list.scorecards[0].fidelity).toBe("empty");
    expect(list.scorecards[0].checks["APE-01"]).toBe("desconocido");
    expect(list.scorecards[0].counts.pasa).toBe(0);
  });
});

describe("scorecardDetailSchema", () => {
  it("parsea el detalle real: scorecard + resultados + trayectoria + legado", () => {
    const d = scorecardDetailSchema.parse(detailFixture);
    expect(d.stored).toBe(true);
    expect(d.scorecard?.results.find((r) => r.check_id === "TAG-01")?.turn).toBe(10);
    expect(d.scorecard?.results.filter((r) => r.verdict === "desconocido").length).toBe(1);
    expect(d.scorecard?.results.filter((r) => r.source === "judge").length).toBe(5);
    const turns = d.trajectory?.turns ?? [];
    expect(turns).toHaveLength(10);
    expect(turns[8].discarded_narration[0]).toMatch(/formulario/);
    expect(turns[8].intents).toContain("shipping_flow");
    expect(turns[9].state.route).toBe("humano");
    expect(turns[9].state.changes.map((c) => c.tag)).toEqual([
      "CONFIRMADO_SIN_DATOS",
      "HUMANO",
    ]);
    expect(d.legacy?.avg).toBe(0.93);
  });

  it("acepta scorecard y trayectoria ausentes (episodio aún sin evaluar)", () => {
    const d = scorecardDetailSchema.parse({ stored: false, scorecard: null });
    expect(d.scorecard).toBeNull();
    expect(d.trajectory).toBeNull();
    expect(d.legacy).toBeNull();
  });

  it("tolera un estado de turno malformado", () => {
    const d = scorecardDetailSchema.parse({
      stored: true,
      trajectory: {
        session_id: "wa_570000000009",
        episode_id: "ep_001",
        turns: [{ turn: 1, state: { changes: "roto" }, tools: [{ name: "x" }] }],
      },
    });
    const t = d.trajectory!.turns[0];
    expect(t.state.changes).toEqual([]);
    expect(t.tools[0].ok).toBeNull();
    expect(t.sent_texts).toEqual([]);
  });
});
