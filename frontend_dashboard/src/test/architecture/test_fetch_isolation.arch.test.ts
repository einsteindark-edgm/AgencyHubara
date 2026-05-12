/**
 * Test #7 — fetch() isolation.
 *
 * `fetch(...)` is allowed only inside `src/shared/api/client.ts` (the wrapper)
 * and inside tests (where it may be mocked). Everywhere else, code calls
 * `apiClient` from `shared/api/`. This isolates HTTP I/O from UI/state code.
 *
 * Detection: regex on `\bfetch\s*\(` matches the call site (parens distinguish
 * from `fetchSomething` symbols).
 */
import { describe, expect, it } from "vitest";

import {
  FETCH_CALL_ALLOWLIST,
  iterSrcFiles,
  readUtf8,
  relToFrontend,
} from "./helpers";

const FETCH_CALL_REGEX = /\bfetch\s*\(/;

describe("R-FETCH — fetch() isolation", () => {
  it("only the apiClient wrapper (and tests) may call fetch() directly", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      if (FETCH_CALL_ALLOWLIST.has(rel)) continue;
      if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
      if (rel.startsWith("src/test/")) continue; // setup, fixtures

      const text = readUtf8(abs);
      // Eliminate comments and strings before matching to avoid false positives.
      const stripped = stripCommentsAndStrings(text);
      if (FETCH_CALL_REGEX.test(stripped)) {
        offenders.push(rel);
      }
    }

    expect(
      offenders,
      `R-FETCH violations — fetch() called outside shared/api/client.ts:\n` +
        offenders.map((p) => `  - ${p}`).join("\n") +
        `\n\nAll HTTP must go through apiClient.{get,post,put,delete} from ` +
        `@/shared/api. If you need a new verb or behavior, extend the wrapper.`,
    ).toEqual([]);
  });
});

function stripCommentsAndStrings(src: string): string {
  // Cheap approximation — adequate for arch heuristics, not a real parser.
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")          // block comments
    .replace(/\/\/[^\n]*/g, "")                 // line comments
    .replace(/(["'`])(?:\\.|(?!\1).)*\1/g, '""'); // string literals
}
