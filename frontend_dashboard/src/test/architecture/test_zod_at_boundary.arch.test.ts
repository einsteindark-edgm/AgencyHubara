/**
 * Test #8 — Zod at the HTTP boundary.
 *
 * Every call to `apiClient.get<unknown>(...)`, `apiClient.post<unknown>(...)`, etc.
 * inside `entities/<x>/api.ts` (and `shared/api/`) MUST be paired with a Zod
 * validation (`.parse(...)` or `.safeParse(...)`) before the data flows further.
 *
 * Heuristic (cheap, conservative): for each `entities/<x>/api.{ts,tsx}` file, count
 *   - apiClient.<method>(...) calls (the HTTP boundary)
 *   - .parse(...) and .safeParse(...) call sites
 * If parse calls < apiClient calls, the file is missing Zod validation.
 *
 * Notes:
 *   - `subscribeSse` consumers also need Zod; we treat `.safeParse` on raw
 *     payloads inside an entity api.ts as covering both paths.
 *   - The heuristic is intentionally count-based (not call-graph): false
 *     positives go to an allow-list if they ever appear.
 */
import { describe, expect, it } from "vitest";

import { iterSrcFiles, readUtf8, relToFrontend } from "./helpers";

const API_CLIENT_CALL = /\bapiClient\s*\.\s*(?:get|post|put|delete)\b/g;
const ZOD_PARSE_CALL = /\.\s*(?:safeParse|parse)\s*\(/g;

const FILES_REQUIRING_BOUNDARY = [
  "entities/<x>/api.{ts,tsx}",
  "entities/*/api/*.{ts,tsx}", // future-proof: if api.ts ever becomes api/*.ts
];

describe("R-ZOD — Zod validation at the HTTP boundary", () => {
  it("every apiClient call in entities/<x>/api.ts is paired with a parse/safeParse", () => {
    const offenders: string[] = [];

    for (const glob of FILES_REQUIRING_BOUNDARY) {
      for (const abs of iterSrcFiles(glob)) {
        const rel = relToFrontend(abs);
        if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
        const text = readUtf8(abs);

        const apiCalls = (text.match(API_CLIENT_CALL) ?? []).length;
        const zodParses = (text.match(ZOD_PARSE_CALL) ?? []).length;

        if (apiCalls === 0) continue;
        if (zodParses < apiCalls) {
          offenders.push(
            `${rel} → ${apiCalls} apiClient call(s) but only ${zodParses} ` +
              `parse/safeParse call(s). Every HTTP read must be validated by Zod.`,
          );
        }
      }
    }

    expect(
      offenders,
      `R-ZOD violations — missing schema.parse(raw) after apiClient.<verb>:\n` +
        offenders.map((m) => `  - ${m}`).join("\n") +
        `\n\nThe compile-time <T> on apiClient.get<T> is documentation only; ` +
        `the Zod parse is enforcement. Add the schema in entities/<x>/contracts.ts ` +
        `and call \`schema.parse(raw)\` (or .safeParse for fault-tolerant paths).`,
    ).toEqual([]);
  });
});
