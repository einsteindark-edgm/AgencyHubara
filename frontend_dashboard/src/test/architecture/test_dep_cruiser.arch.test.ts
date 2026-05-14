/**
 * Tests #1–#6 — FSD layering / barrel / no-cross-layer-relative.
 *
 * These 6 contracts live declaratively in `.dependency-cruiser.cjs`. The test
 * runs `depcruise` as a subprocess and asserts exit code 0. Failure output is
 * the depcruise report itself, so the operator sees the exact violating edge.
 */
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";

import { FRONTEND_ROOT } from "./helpers";

describe("FSD layering — dependency-cruiser contracts", () => {
  // depcruise corre como subprocess (npx → arranque slow). Timeout default
  // de 5s es muy ajustado a medida que crece el grafo (152+ módulos a 2026-05).
  // 30s da margen sin esconder cuelgues reales.
  it("layers + barrels + cross-layer-relative rules pass", { timeout: 30_000 }, () => {
    let stdout = "";
    let stderr = "";
    let exitCode = 0;
    try {
      stdout = execFileSync(
        "npx",
        ["depcruise", "--no-progress", "--config", ".dependency-cruiser.cjs", "src"],
        {
          cwd: FRONTEND_ROOT,
          encoding: "utf-8",
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
    } catch (err) {
      const e = err as { status?: number; stdout?: string; stderr?: string };
      exitCode = e.status ?? 1;
      stdout = e.stdout ?? "";
      stderr = e.stderr ?? "";
    }
    if (exitCode !== 0) {
      throw new Error(
        "FSD layering violations — `depcruise` reported broken contracts:\n" +
          "--- stdout ---\n" +
          stdout +
          "\n--- stderr ---\n" +
          stderr +
          "\nFix the offending import, or — if intentional — add an entry to " +
          "`.dependency-cruiser.cjs` with a documented reason.",
      );
    }
    expect(exitCode).toBe(0);
  });
});
