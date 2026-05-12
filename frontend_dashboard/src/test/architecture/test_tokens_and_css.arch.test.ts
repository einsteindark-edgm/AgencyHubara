/**
 * Tests #9 + #15 — Tailwind tokens naming + CSS file location.
 *
 *  #9  Tailwind tokens at `src/index.css` `@theme` block must not collide with
 *      utility classes. Anti-pattern #13: `--color-text-X` would generate a
 *      `text-text-X` Tailwind utility — confusing and lint-flagged. Use
 *      `--color-fg`, `--color-fg-muted` instead.
 *
 *  #15 No `.css` files anywhere under `src/` except `src/index.css`. Per-feature
 *      styling stays in the feature (Tailwind utilities). Centralizing CSS in
 *      one file keeps the design surface auditable.
 */
import { describe, expect, it } from "vitest";

import { CSS_FILE_ALLOWLIST, iterSrcFiles, readUtf8, relToFrontend } from "./helpers";

const FORBIDDEN_TOKEN_PATTERNS: ReadonlyArray<{ regex: RegExp; reason: string }> = [
  {
    regex: /--color-text-[a-zA-Z0-9_-]+/g,
    reason:
      "uses `--color-text-X` (collides with `text-*` Tailwind utility — generates `text-text-X`)",
  },
];

describe("R-TAILWIND — token naming + CSS file scope", () => {
  it("index.css tokens do not use forbidden naming", () => {
    const cssFiles = iterSrcFiles("**/*.css");
    const offenders: string[] = [];

    for (const abs of cssFiles) {
      const rel = relToFrontend(abs);
      const text = readUtf8(abs);
      for (const { regex, reason } of FORBIDDEN_TOKEN_PATTERNS) {
        const matches = text.match(regex);
        if (matches && matches.length > 0) {
          offenders.push(
            `${rel} — found ${matches.length} token(s) that ${reason}: ${[
              ...new Set(matches),
            ].join(", ")}`,
          );
        }
      }
    }

    expect(
      offenders,
      `Tailwind token naming violations:\n` +
        offenders.map((m) => `  - ${m}`).join("\n") +
        `\n\nRename to --color-fg / --color-fg-muted / --color-accent etc. ` +
        `See frontend-tech-refiner-archon SKILL.md anti-pattern #13.`,
    ).toEqual([]);
  });

  it("no .css files exist under src/ except CSS_FILE_ALLOWLIST", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.css")) {
      const rel = relToFrontend(abs);
      if (!CSS_FILE_ALLOWLIST.has(rel)) {
        offenders.push(rel);
      }
    }

    expect(
      offenders,
      `Per-feature CSS files are not allowed in FSD — use Tailwind utilities:\n` +
        offenders.map((p) => `  - ${p}`).join("\n"),
    ).toEqual([]);
  });
});
