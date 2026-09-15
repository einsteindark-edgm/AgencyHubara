import { describe, expect, it } from "vitest";

import calibrationFixture from "./fixtures/calibration.json";
import createdFixture from "./fixtures/label-created.json";
import labelsFixture from "./fixtures/labels.json";
import queueFixture from "./fixtures/labels-queue.json";
import {
  calibrationSchema,
  createLabelResponseSchema,
  labelQueueSchema,
  labelsListSchema,
} from "./contracts";

describe("contratos de etiquetas humanas", () => {
  it("parsea las etiquetas de un episodio", () => {
    const l = labelsListSchema.parse(labelsFixture);
    expect(l.labels).toHaveLength(2);
    expect(l.labels[0]).toMatchObject({ check_id: "CON-01", verdict: "falla" });
  });

  it("parsea la respuesta al crear una etiqueta", () => {
    const r = createLabelResponseSchema.parse(createdFixture);
    expect(r.ok).toBe(true);
    expect(r.label.check_id).toBe("DES-04");
  });

  it("parsea la cola de etiquetado con el veredicto del juez", () => {
    const q = labelQueueSchema.parse(queueFixture);
    expect(q.items.map((i) => i.reason)).toEqual(["desconocido", "falla", "muestra"]);
    expect(q.items[1].judge_verdict).toBe("falla");
  });

  it("parsea la calibración con métricas nulas y estados", () => {
    const c = calibrationSchema.parse(calibrationFixture);
    expect(c.min_labels).toBe(20);
    expect(c.kappa_threshold).toBe(0.6);
    expect(c.checks.find((x) => x.check_id === "CON-04")).toMatchObject({
      n: 0,
      tpr: null,
      kappa: null,
      status: "sin_datos",
    });
  });

  it("degrada estados de calibración desconocidos a sin_datos", () => {
    const c = calibrationSchema.parse({ checks: [{ check_id: "X", status: "raro" }] });
    expect(c.checks[0].status).toBe("sin_datos");
    expect(c.checks[0].tp).toBe(0);
  });
});
