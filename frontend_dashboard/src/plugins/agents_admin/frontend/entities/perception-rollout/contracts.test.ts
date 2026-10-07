import { describe, expect, it } from "vitest";

import { decisionEngineSchema, rolloutSchema } from "./contracts";

describe("rolloutSchema", () => {
  it("tolera un cuerpo parcial y degrada a apagado (L-10)", () => {
    const r = rolloutSchema.parse({ state: { mode: "raro" }, ceiling: "shadow" });

    expect(r.state.mode).toBe("off");
    expect(r.ceiling).toBe("shadow");
    expect(r.metrics).toEqual({ days: 0, turns: 0, fallback_rate: null, p95_ms: null });
    expect(r.can).toEqual({});
  });

  it("lee el contrato completo de chats", () => {
    const r = rolloutSchema.parse({
      state: { mode: "canary", canary_percent: 5, test_numbers: ["wa_573001234567"], updated_at_ms: 1, updated_by: "dashboard" },
      ceiling: "on",
      profile: "jev-v1",
      metrics: { days: 8, turns: 900, fallback_rate: 0.002, p95_ms: 640 },
      readiness: { on: [{ code: "shadow_days", ok: true, detail: "8 días" }] },
      can: { on: [] },
    });

    expect(r.state.test_numbers).toEqual(["wa_573001234567"]);
    expect(r.readiness.on[0].ok).toBe(true);
  });
});

describe("rolloutSchema — motor de decisiones (F7)", () => {
  it("lee el interruptor de cada capacidad y la versión del workflow", () => {
    const r = rolloutSchema.parse({
      state: { mode: "off" },
      ceiling: "on",
      capabilities: {
        baja: { mode: "shadow", ceiling: "on", readiness: { canary: [{ code: "shadow_days", ok: false, detail: "2 días" }] },
                can: { canary: ["shadow_days"] } },
      },
      workflow_v2: { mode: "canary", ceiling: "on", readiness: {}, can: { on: [] } },
    });

    expect(r.capabilities.baja.mode).toBe("shadow");
    expect(r.capabilities.baja.can.canary).toEqual(["shadow_days"]);
    expect(r.workflow_v2.mode).toBe("canary");
  });

  it("sin los campos nuevos (API vieja) degrada a todo en reglas y V1", () => {
    const r = rolloutSchema.parse({ state: { mode: "off" }, ceiling: "off" });

    expect(r.capabilities).toEqual({});
    expect(r.workflow_v2.mode).toBe("off");
  });
});

describe("decisionEngineSchema — los paquetes que corren en el motor", () => {
  it("lee el paquete de la tienda, el de la App Operador y de cuál sale cada decisión", () => {
    const r = decisionEngineSchema.parse({
      bundle: { id: "ventas", version: 1, ref: "ventas@1", oracle: "jev-1.13", engine_contract: 1, code_default: "ventas" },
      bundles: [
        { id: "ventas", version: 1, ref: "ventas@1", oracle: "jev-1.13", name: "La tienda: el bot de ventas y remarketing" },
        { id: "operador", version: 1, ref: "operador@1", oracle: "jev-1.13", name: "App Operador: el chat y los incendios del teléfono" },
      ],
      places: [{ id: "chat_operador", label: "En el chat de la App Operador" }],
      decisions: [{ capability: "burbuja", name: "Qué acción va primero", where: ["chat_operador"], solves: "…", mode: "shadow", bundle: "operador@1" }],
    });

    expect(r.bundles.map((b) => b.ref)).toEqual(["ventas@1", "operador@1"]);
    expect(r.decisions[0].bundle).toBe("operador@1");
  });

  it("sin la lista de paquetes (un backend anterior) queda vacía", () => {
    const r = decisionEngineSchema.parse({ bundle: { id: "ventas" }, places: [], decisions: [{ capability: "baja", mode: "off" }] });

    expect(r.bundles).toEqual([]);
    expect(r.decisions[0].bundle).toBe("");
  });
});
