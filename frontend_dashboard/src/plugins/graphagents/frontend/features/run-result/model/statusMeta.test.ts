import { describe, expect, it } from "vitest";

import type { RunStatus } from "@plugins/graphagents/frontend/entities/graphagents-run";

import { statusMeta } from "./statusMeta";

describe("statusMeta", () => {
  it("devuelve label + color para cada status del enum", () => {
    const statuses: RunStatus[] = [
      "pending",
      "running",
      "awaiting_approval",
      "completed",
      "failed",
    ];
    for (const s of statuses) {
      const m = statusMeta(s);
      expect(m.label).toBeTruthy();
      expect(m.color).toMatch(/^var\(--color-/);
    }
  });

  it("mapea los terminales a ok/danger", () => {
    expect(statusMeta("completed").color).toBe("var(--color-ok)");
    expect(statusMeta("failed").color).toBe("var(--color-danger)");
    expect(statusMeta("awaiting_approval").color).toBe("var(--color-warn)");
  });
});
