import { describe, expect, it } from "vitest";

import type { CouponChange } from "@plugins/marketing/frontend/entities/coupon";

import { changeActionLabel, changeSummary } from "./changes";

function change(action: string, detail: Record<string, unknown>): CouponChange {
  return { ts: "2026-09-24T15:00:00Z", actor: "ana@hubara.co", action, detail };
}

describe("registro de cambios — escrituras que Medusa no confirmó (premortem A4/A8/A15)", () => {
  it.each([
    ["update_unconfirmed", /no confirmó/i],
    ["set_status_unconfirmed", /no confirmó/i],
    ["delete_unconfirmed", /no confirmó/i],
    ["units_pruned", /unidades/i],
  ])("la acción %s se lee en español", (action, label) => {
    expect(changeActionLabel(action)).toMatch(label);
    expect(changeActionLabel(action)).not.toBe(action);
  });

  it("una edición a medias sin estado releído dice qué pasos se aplicaron y que no se pudo ver cómo quedó", () => {
    const lines = changeSummary(
      change("update_partial", {
        percentage: [10, 15],
        failed_step: "campaign",
        applied_steps: "promotion, products",
        state_unknown: true,
      }),
    );

    expect(lines).toContain("paso que falló: campaña y fechas");
    expect(lines).toContain("pasos aplicados: código y descuento, productos");
    expect(lines).toContain("cómo quedó en Medusa: no se pudo leer");
  });

  it("el borrado cuenta la campaña que quedó y la limpieza de un cupón que ya no estaba", () => {
    expect(changeSummary(change("delete", { orphaned_campaign_id: "camp_1" }))).toEqual([
      "campaña que quedó en Medusa: camp_1",
    ]);
    expect(
      changeSummary(
        change("delete", { already_deleted: true, units_deleted: true, orphan_campaigns_deleted: ["camp_2"] }),
      ),
    ).toEqual([
      "ya estaba borrado en Medusa: sí",
      "unidades borradas: sí",
      "campañas huérfanas borradas: camp_2",
    ]);
  });
});
