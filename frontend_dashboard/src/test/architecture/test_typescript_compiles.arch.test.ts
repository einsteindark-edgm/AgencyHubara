/**
 * Test #17 — TypeScript builds cleanly (smoke).
 *
 * Runs `tsc -b --noEmit` against tsconfig.app.json (the dev config Vite uses).
 * Catches the kind of breakage where a refactor leaves a dangling type import,
 * a renamed symbol that wasn't updated in all call sites, or a barrel export
 * pointing to a deleted file.
 *
 * The cost is ~2-4s on a warm build cache — worth it as a final gate.
 */
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";

import { FRONTEND_ROOT } from "./helpers";

describe("R-SMOKE — TypeScript build", () => {
  it("tsc -b --noEmit succeeds for tsconfig.app.json", () => {
    let exitCode = 0;
    let stdout = "";
    let stderr = "";
    try {
      stdout = execFileSync(
        "npx",
        ["tsc", "-b", "--noEmit", "--pretty", "false", "tsconfig.app.json"],
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
        `R-SMOKE — tsc -b reported type errors:\n` +
          `--- stdout ---\n${stdout}\n` +
          `--- stderr ---\n${stderr}\n`,
      );
    }
    expect(exitCode).toBe(0);
  }, 60_000); // generous timeout — cold build can take a while
});
