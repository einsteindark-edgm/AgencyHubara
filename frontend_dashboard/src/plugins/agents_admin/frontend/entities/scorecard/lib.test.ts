import { describe, expect, it } from "vitest";

import checksFixture from "./fixtures/checks.json";
import detailFixture from "./fixtures/scorecard-detail.json";
import { checkRegistrySchema, scorecardDetailSchema } from "./contracts";
import {
  buildStripModel,
  checkStatus,
  compareEpisodeVerdict,
  episodeLabel,
  episodeVerdictLabel,
  failurePointLabel,
  fidelityLabel,
  formatCompliance,
  formatDuration,
  groupResultsByFamily,
  levelLabel,
  stageColor,
  stageLabel,
  statusGlyph,
  statusLabel,
  summarizeArgs,
} from "./lib";
import type { CheckResult, Trajectory } from "./model";

const registry = checkRegistrySchema.parse(checksFixture);
const detail = scorecardDetailSchema.parse(detailFixture);
const results = detail.scorecard!.results;
const trajectory = detail.trajectory!;

describe("etiquetas", () => {
  it("nombra las etapas con acentos y espacios", () => {
    expect(stageLabel("descubrimiento")).toBe("descubrimiento");
    expect(stageLabel("confirmacion")).toBe("confirmación");
    expect(stageLabel("datos_envio")).toBe("datos de envío");
    expect(stageLabel("postcierre")).toBe("post-cierre");
    expect(stageLabel("transversal")).toBe("transversal");
    expect(stageLabel("etapa_nueva")).toBe("etapa nueva");
    expect(stageLabel(null)).toBe("sin etapa");
  });

  it("nombra veredictos, niveles y fidelidad", () => {
    expect(episodeVerdictLabel("SIN_DATOS")).toBe("Sin datos");
    expect(levelLabel("critico")).toBe("crítico");
    expect(fidelityLabel("trace")).toBe("traza completa");
    expect(fidelityLabel("legacy")).toBe("reconstrucción legada — datos parciales");
  });

  it("ordena veredictos de episodio FALLA → ALERTA → PASA → SIN_DATOS", () => {
    const sorted = (["PASA", "SIN_DATOS", "FALLA", "ALERTA"] as const)
      .slice()
      .sort(compareEpisodeVerdict);
    expect(sorted).toEqual(["FALLA", "ALERTA", "PASA", "SIN_DATOS"]);
  });

  it("formatea cumplimiento y duraciones", () => {
    expect(formatCompliance(0.6667)).toBe("67 %");
    expect(formatCompliance(null)).toBe("—");
    expect(formatDuration(8_280_000)).toBe("2 h 18 min");
    expect(formatDuration(300_000)).toBe("5 min");
    expect(formatDuration(40_000)).toBe("40 s");
  });

  it("describe el primer fallo con turno e id", () => {
    expect(failurePointLabel({ turn: 5, check_id: "VAR-01" })).toBe("turno 5 · VAR-01");
    expect(failurePointLabel({ turn: null, check_id: "GHO-01" })).toBe("sin turno · GHO-01");
    expect(failurePointLabel(null)).toBeNull();
  });

  it("acorta la sesión en la etiqueta del episodio", () => {
    expect(episodeLabel({ session_id: "wa_100000000001", episode_id: "ep_007" })).toBe(
      "wa_…0000001 · ep_007",
    );
    expect(episodeLabel({ session_id: "local", episode_id: "" })).toBe("local");
  });

  it("resume args de tools en una línea", () => {
    expect(summarizeArgs({ producto: "cubo-love", color: "Azul" })).toBe(
      "producto=cubo-love · color=Azul",
    );
    expect(summarizeArgs({})).toBe("");
    expect(summarizeArgs({ body: "x".repeat(200) }, 20)).toHaveLength(20);
  });
});

describe("checkStatus", () => {
  it("una falla toma el color de su nivel; el resto su veredicto", () => {
    expect(checkStatus("falla", "critico")).toBe("critico");
    expect(checkStatus("falla", "menor")).toBe("menor");
    expect(checkStatus("pasa", "critico")).toBe("pasa");
    expect(checkStatus("no_aplica", "mayor")).toBe("no_aplica");
    expect(checkStatus("desconocido", "mayor")).toBe("desconocido");
    expect(checkStatus(undefined, "mayor")).toBe("sin_resultado");
  });

  it("cada estado tiene glifo y texto: la identidad nunca es solo color", () => {
    expect(statusGlyph("pasa")).toBe("✓");
    expect(statusGlyph("mayor")).toBe("✗");
    expect(statusGlyph("no_aplica")).toBe("–");
    expect(statusGlyph("desconocido")).toBe("?");
    expect(statusLabel("critico")).toBe("falla crítica");
    expect(statusLabel("sin_resultado")).toBe("sin evaluar");
  });
});

describe("groupResultsByFamily", () => {
  it("agrupa por familia en el orden del registro, fallas primero", () => {
    const groups = groupResultsByFamily(registry, results);
    expect(groups.map((g) => g.id).slice(0, 3)).toEqual([
      "apertura",
      "descubrimiento",
      "variantes",
    ]);
    const estado = groups.find((g) => g.id === "estado")!;
    expect(estado.label).toBe("Etiquetado y escalación");
    expect(estado.items.slice(0, 2).map((i) => i.checkId)).toEqual(["TAG-01", "TAG-02"]);
    expect(estado.items[0].status).toBe("critico");
  });

  it("incluye los checks del registro sin resultado (p. ej. juez apagado) como sin evaluar", () => {
    const groups = groupResultsByFamily(registry, results);
    const des = groups.find((g) => g.id === "descubrimiento")!;
    const des07 = des.items.find((i) => i.checkId === "DES-07")!;
    expect(des07.result).toBeNull();
    expect(des07.status).toBe("sin_resultado");
    expect(des.items.at(-1)!.status).toBe("sin_resultado");
  });

  it("omite los sin evaluar cuando se pide", () => {
    const groups = groupResultsByFamily(registry, results, { includeUnevaluated: false });
    const all = groups.flatMap((g) => g.items);
    expect(all).toHaveLength(results.length);
  });

  it("manda a 'Otros' los resultados de checks que el registro no conoce", () => {
    const extra: CheckResult = {
      check_id: "ZZZ-01",
      verdict: "falla",
      level: "mayor",
      turn: 2,
      evidence: "",
      critique: "",
      source: "code",
      topics: [],
    };
    const groups = groupResultsByFamily(registry, [extra], { includeUnevaluated: false });
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Otros");
    expect(groups[0].items[0].check).toBeNull();
  });
});

describe("buildStripModel", () => {
  const model = buildStripModel(trajectory, results, registry);

  it("una columna por turno con su etapa (stage_out, o stage_in)", () => {
    expect(model.columns).toHaveLength(10);
    expect(model.columns[5].stage).toBe("variantes");
    expect(model.columns[8].stage).toBe("confirmacion");
  });

  it("agrupa turnos contiguos de la misma etapa en bandas", () => {
    expect(model.bands.map((b) => [b.stage, b.from, b.to])).toEqual([
      ["descubrimiento", 0, 4],
      ["variantes", 5, 7],
      ["confirmacion", 8, 9],
    ]);
    expect(model.bands[2].label).toBe("confirmación");
  });

  it("separa carriles: cliente, bot, tools, componentes, estado y guardas", () => {
    const t9 = model.columns[8];
    expect(t9.lanes.cliente.map((c) => c.kind)).toEqual(["handoff", "signal"]);
    expect(t9.lanes.cliente[1].text).toBe("señal · aplazamiento");
    expect(t9.lanes.bot).toEqual([
      expect.objectContaining({
        kind: "discarded",
        text: "Perfecto, ya casi llegas a casa. Te dejo el formulario.",
      }),
    ]);
    expect(t9.lanes.bot[0].detail).toMatch(/descartad/i);
    expect(t9.lanes.tools.map((c) => c.text)).toEqual([
      "set_order_slot",
      "request_shipping_details",
    ]);
    expect(t9.lanes.componentes.map((c) => c.text)).toEqual(["shipping_flow"]);
    const t10 = model.columns[9];
    expect(t10.lanes.estado.map((c) => c.text)).toEqual([
      "CONFIRMADO_SIN_DATOS",
      "HUMANO",
      "ruta humano",
    ]);
    // TAG-01/TAG-02 (familia estado) fallan en ese turno → estado en alerta.
    expect(t10.lanes.estado.every((c) => c.alert)).toBe(true);
    expect(model.columns[7].lanes.estado[0]).toMatchObject({ text: "INTERESADO", alert: false });
    expect(model.columns[7].lanes.cliente[0].kind).toBe("system");
  });

  it("marca tools rechazadas con su error", () => {
    const traj: Trajectory = {
      ...trajectory,
      turns: [
        {
          ...trajectory.turns[0],
          tools: [
            { name: "register_order", ok: false, error: "purchase_not_confirmed", notes: [], args: {} },
          ],
        },
      ],
    };
    const m = buildStripModel(traj, []);
    expect(m.columns[0].lanes.tools[0].kind).toBe("tool_rejected");
    expect(m.columns[0].lanes.tools[0].detail).toMatch(/purchase_not_confirmed/);
  });

  it("ancla cada check a su turno y los sin turno al último", () => {
    expect(model.columns[4].checks.map((c) => c.checkId)).toEqual(["VAR-01"]);
    const last = model.columns[9];
    expect(last.checks.find((c) => c.checkId === "GHO-01")?.anchored).toBe(false);
    expect(last.checks.find((c) => c.checkId === "TAG-01")?.anchored).toBe(true);
    // Fallas primero dentro del turno.
    expect(last.checks[0].status).toBe("critico");
    // No aplica no se dibuja en la tira (no juzga ningún evento).
    expect(model.columns.flatMap((c) => c.checks).some((c) => c.verdict === "no_aplica")).toBe(false);
  });

  it("marca primer fallo y primer crítico", () => {
    expect(model.firstFailure).toEqual({ turn: 2, checkId: "DES-09" });
    expect(model.firstCritical).toEqual({ turn: 5, checkId: "VAR-01" });
    expect(model.columns[1].isFirstFailure).toBe(true);
    expect(model.columns[4].isFirstCritical).toBe(true);
    expect(model.columns.filter((c) => c.isFirstFailure)).toHaveLength(1);
  });

  it("distingue primer fallo de primer crítico cuando difieren", () => {
    const rs: CheckResult[] = [
      { check_id: "DES-01", verdict: "falla", level: "mayor", turn: 2, evidence: "", critique: "", source: "code", topics: [] },
      { check_id: "CON-01", verdict: "falla", level: "critico", turn: 4, evidence: "", critique: "", source: "code", topics: [] },
    ];
    const m = buildStripModel(trajectory, rs);
    expect(m.firstFailure?.checkId).toBe("DES-01");
    expect(m.firstCritical?.checkId).toBe("CON-01");
    expect(m.columns[1].isFirstFailure).toBe(true);
    expect(m.columns[3].isFirstCritical).toBe(true);
  });

  it("señala huecos largos entre turnos", () => {
    // turno 8 (480 000 ms) → turno 9 (20 000 000 ms): 5 h 25 min.
    expect(model.columns[8].gapBeforeMs).toBe(19_520_000);
    expect(model.columns[1].gapBeforeMs).toBeNull();
  });

  it("resuelve para la vista el color de cada banda y la nota del disparador", () => {
    expect(model.bands.map((b) => b.color)).toEqual(model.bands.map((b) => stageColor(b.stage)));
    const ghost = model.columns[7];
    expect(ghost.trigger).toBe("ghost");
    expect(ghost).toMatchObject({ triggerNote: "ghosting", botSilent: true });
    expect(model.columns[8]).toMatchObject({ trigger: "handoff", triggerNote: "handoff", botSilent: false });
    expect(model.columns[0]).toMatchObject({ trigger: "customer", triggerNote: null, botSilent: false });
  });

  it("devuelve un modelo vacío sin turnos", () => {
    const m = buildStripModel({ ...trajectory, turns: [] }, results);
    expect(m.columns).toEqual([]);
    expect(m.bands).toEqual([]);
  });
});
