/**
 * Tests #13 + #14 — env reads centralized + no hardcoded API URLs.
 *
 *  #13 `import.meta.env.*` and `process.env.*` reads only inside
 *      `src/shared/config/env.ts`. Everywhere else, code uses `env` from
 *      `@/shared/config/env`. Centralization is what makes env documented,
 *      testable (vitest.config.ts overrides), and changeable in one place.
 *
 *  #14 No hardcoded `http://` / `https://` URL literals outside
 *      `src/shared/config/` and `src/index.css` (Google Fonts). All HTTP
 *      targets pass through `env.apiUrl`. Tests are exempted (mock URLs).
 */
import { describe, expect, it } from "vitest";

import {
  ENV_READ_ALLOWLIST,
  HARDCODED_URL_ALLOWLIST,
  iterSrcFiles,
  readUtf8,
  relToFrontend,
} from "./helpers";

// Only custom env vars must go through shared/config/env.ts.
// Vite built-in flags (DEV / PROD / MODE / SSR / BASE_URL) are part of the
// runtime contract — accessing them directly is fine anywhere.
const ENV_READ_PATTERNS: ReadonlyArray<RegExp> = [
  /\bimport\.meta\.env\.(?!DEV\b|PROD\b|MODE\b|SSR\b|BASE_URL\b)\w+/,
  /\bprocess\.env\.\w+/,
];

const URL_LITERAL_REGEX = /\bhttps?:\/\/[^\s"'`<>]+/g;

describe("R-ENV — env access + URL literals", () => {
  it("import.meta.env / process.env reads live only in shared/config/env.ts", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      if (ENV_READ_ALLOWLIST.has(rel)) continue;
      if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
      if (rel.startsWith("src/test/")) continue;
      const text = stripCommentsAndStrings(readUtf8(abs));
      for (const re of ENV_READ_PATTERNS) {
        if (re.test(text)) {
          offenders.push(`${rel} reads env directly (use \`env\` from @/shared/config/env)`);
          break;
        }
      }
    }

    expect(
      offenders,
      `R-ENV violations — env access outside shared/config/env.ts:\n` +
        offenders.map((m) => `  - ${m}`).join("\n"),
    ).toEqual([]);
  });

  it("no hardcoded http(s) URLs outside the env layer", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.{ts,tsx,css}")) {
      const rel = relToFrontend(abs);
      if (HARDCODED_URL_ALLOWLIST.has(rel)) continue;
      if (rel.startsWith("src/shared/config/")) continue;
      if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
      if (rel.startsWith("src/test/")) continue;

      const text = stripCommentsAndStrings(readUtf8(abs));
      const matches = text.match(URL_LITERAL_REGEX);
      if (matches && matches.length > 0) {
        const unique = [...new Set(matches)].slice(0, 3);
        offenders.push(`${rel} → ${unique.join(", ")}`);
      }
    }

    expect(
      offenders,
      `Hardcoded URL violations — use env.apiUrl or env.<...> from shared/config/env.ts:\n` +
        offenders.map((m) => `  - ${m}`).join("\n"),
    ).toEqual([]);
  });
});

function stripCommentsAndStrings(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
  // NOTE: do NOT strip string literals — URL literals live inside strings.
}
