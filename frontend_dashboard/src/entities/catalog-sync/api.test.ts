/**
 * Tests de los Zod schemas de `catalog-sync` (primera línea de defensa contra
 * contract drift con `hubara_agency/src/plugins/catalog/api`) + los helpers de
 * presentación puros (`model.ts`).
 */

import { describe, expect, it } from "vitest";
import {
  snapshotInfoSchema,
  syncDetailSchema,
  syncHistoryResponseSchema,
  triggerSyncResponseSchema,
} from "./contracts";
import {
  formatRelativeTime,
  isSyncActive,
  syncStatusLabel,
  syncStatusTone,
} from "./model";

const sampleSteps = [
  { key: "pull", label: "Traer productos de Medusa", status: "done", detail: "248 productos" },
  { key: "write", label: "Escribir copia local (snapshot)", status: "running", detail: "" },
  { key: "push", label: "Propagar a Meta Commerce Catalog", status: "pending", detail: "" },
];

describe("syncDetailSchema", () => {
  it("accepts a running sync with live steps", () => {
    const valid = {
      workflow_id: "catalog-sync-on-demand-dashboard-1717",
      status: "running",
      started_at_ms: 1779800400000,
      finished_at_ms: null,
      product_count: 248,
      version: "",
      error: null,
      steps: sampleSteps,
    };
    expect(() => syncDetailSchema.parse(valid)).not.toThrow();
  });

  it("accepts a completed sync with a skipped Meta step", () => {
    const valid = {
      workflow_id: "wf-1",
      status: "completed",
      started_at_ms: 1779800400000,
      finished_at_ms: 1779800460000,
      product_count: 248,
      version: "abc123",
      error: null,
      steps: [
        { key: "pull", label: "Traer productos de Medusa", status: "done", detail: "248 productos" },
        { key: "write", label: "Escribir copia local (snapshot)", status: "done", detail: "250 archivos · vabc123" },
        { key: "push", label: "Propagar a Meta Commerce Catalog", status: "skipped", detail: "omitido — Meta no configurado" },
      ],
    };
    expect(() => syncDetailSchema.parse(valid)).not.toThrow();
  });

  it("rejects an unknown step status", () => {
    expect(() =>
      syncDetailSchema.parse({
        workflow_id: "wf-1",
        status: "running",
        started_at_ms: null,
        finished_at_ms: null,
        product_count: 0,
        version: "",
        error: null,
        steps: [{ key: "pull", label: "x", status: "frozen", detail: "" }],
      }),
    ).toThrow();
  });
});

describe("syncHistoryResponseSchema", () => {
  it("accepts a populated history", () => {
    const valid = {
      syncs: [
        {
          workflow_id: "catalog-sync-on-demand-dashboard-1717",
          run_id: "01HX",
          status: "completed",
          started_at_ms: 1779800400000,
          finished_at_ms: 1779800460000,
          triggered_by: "dashboard",
        },
      ],
      available: true,
      error_detail: null,
    };
    expect(() => syncHistoryResponseSchema.parse(valid)).not.toThrow();
  });

  it("accepts available=false + empty list (Temporal visibility down)", () => {
    const valid = { syncs: [], available: false, error_detail: "backend_unreachable" };
    expect(() => syncHistoryResponseSchema.parse(valid)).not.toThrow();
  });
});

describe("triggerSyncResponseSchema", () => {
  it("accepts a fresh trigger and an already-running one", () => {
    expect(() =>
      triggerSyncResponseSchema.parse({
        workflow_id: "wf-1",
        run_id: "01HX",
        started_at_ms: 1779800400000,
        status: "running",
        already_running: false,
      }),
    ).not.toThrow();
    expect(() =>
      triggerSyncResponseSchema.parse({
        workflow_id: "wf-1",
        run_id: "",
        started_at_ms: 1779800400000,
        status: "running",
        already_running: true,
      }),
    ).not.toThrow();
  });
});

describe("snapshotInfoSchema", () => {
  it("accepts an existing snapshot", () => {
    const valid = {
      exists: true,
      version: "abc123",
      product_count: 248,
      fetched_at: "2026-06-04T12:00:00+00:00",
      age_minutes: 5,
      stale: false,
      max_age_minutes: 30,
      snapshot_dir: "/app/hubara_vault/catalog",
    };
    expect(() => snapshotInfoSchema.parse(valid)).not.toThrow();
  });

  it("accepts a missing snapshot (no sync ran yet)", () => {
    const valid = {
      exists: false,
      version: null,
      product_count: 0,
      fetched_at: null,
      age_minutes: null,
      stale: true,
      max_age_minutes: 30,
      snapshot_dir: "/app/hubara_vault/catalog",
    };
    expect(() => snapshotInfoSchema.parse(valid)).not.toThrow();
  });
});

describe("model helpers", () => {
  it("isSyncActive only for running", () => {
    expect(isSyncActive("running")).toBe(true);
    expect(isSyncActive("completed")).toBe(false);
    expect(isSyncActive("failed")).toBe(false);
  });

  it("syncStatusLabel maps known states to Spanish", () => {
    expect(syncStatusLabel("running")).toBe("En curso");
    expect(syncStatusLabel("completed")).toBe("Completado");
    expect(syncStatusLabel("failed")).toBe("Falló");
    expect(syncStatusLabel("cancelled")).toBe("Cancelado");
  });

  it("syncStatusTone maps to the kit palette", () => {
    expect(syncStatusTone("running")).toBe("live");
    expect(syncStatusTone("completed")).toBe("ok");
    expect(syncStatusTone("failed")).toBe("err");
    expect(syncStatusTone("unknown")).toBe("mute");
  });

  it("formatRelativeTime handles null and recent timestamps", () => {
    expect(formatRelativeTime(null)).toBe("—");
    expect(formatRelativeTime(Date.now())).toBe("hace un momento");
    expect(formatRelativeTime(Date.now() - 5 * 60_000)).toBe("hace 5 min");
    expect(formatRelativeTime(Date.now() - 3 * 3_600_000)).toBe("hace 3 h");
  });
});
