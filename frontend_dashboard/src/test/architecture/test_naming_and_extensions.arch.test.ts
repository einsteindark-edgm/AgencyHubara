/**
 * Tests #10 + #11 + #12 + #16 — naming / extensions / layout invariants.
 *
 *  #10 JSX only inside `.tsx` files. A `.ts` file containing JSX-like tokens
 *      (`<Component>`, `</`) breaks esbuild with "Expected '>' but found ..."
 *      (FSD anti-pattern: gotcha #7).
 *
 *  #11 No forbidden top-level packages under `src/`: components, utils, lib,
 *      helpers. FSD anti-pattern #11. Components live inside features/shared/ui.
 *
 *  #12 Every `entities/<x>/` and `features/<x>/` slice exposes a barrel via
 *      `index.ts`. Without it, consumers reach into internals (FSD rule #5).
 *
 *  #16 No `<asterisk>/` ending sequence inside JSDoc block comments. esbuild
 *      closes the comment early and the file fails to parse. FSD anti-pattern
 *      #12 — use `<x>` placeholders in glob examples inside comments.
 */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  FORBIDDEN_TOP_LEVEL_FOLDERS,
  SRC_ROOT,
  iterSrcFiles,
  readUtf8,
  relToFrontend,
} from "./helpers";

// JSX-specific patterns (TypeScript generics like `<T>` never match these):
//   - JSX_SELF_CLOSE: `<Foo />`, `<Foo prop="x" />`
//   - JSX_CLOSE_TAG: `</Foo>` — paired closer (generics use `>`, never `</`)
//   - JSX_ATTR: `<Foo attr=` — attributes never appear in generics
//   - JSX_FRAGMENT: `<></>` or `<>`
const JSX_PATTERNS: ReadonlyArray<RegExp> = [
  /<\/[A-Z][A-Za-z0-9]*\s*>/,            // </Foo>
  /<\/>/,                                  // </>
  /<>\s*[^/]/,                             // <>...
  /<[A-Z][A-Za-z0-9]*[^<>]{0,200}\/>/,    // <Foo ... />
  /<[A-Z][A-Za-z0-9]*\s+[a-z][a-zA-Z0-9-]*\s*=/, // <Foo attr=
];

describe("R-NAMING — file extension / layout / barrel invariants", () => {
  it("no JSX-like tokens appear inside .ts files (must be .tsx)", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.ts")) {
      const rel = relToFrontend(abs);
      if (rel.endsWith(".d.ts")) continue;
      // The arch suite itself examines JSX patterns inside regex literals — exempt.
      if (rel.startsWith("src/test/architecture/")) continue;
      const text = stripCommentsAndStrings(readUtf8(abs));
      if (JSX_PATTERNS.some((re) => re.test(text))) {
        offenders.push(rel);
      }
    }

    expect(
      offenders,
      `JSX detected inside .ts files (rename to .tsx):\n` +
        offenders.map((p) => `  - ${p}`).join("\n"),
    ).toEqual([]);
  });

  it("no forbidden top-level packages exist under src/", () => {
    const offenders: string[] = [];
    for (const folder of FORBIDDEN_TOP_LEVEL_FOLDERS) {
      const abs = resolve(SRC_ROOT, folder);
      if (existsSync(abs)) {
        offenders.push(
          `src/${folder}/ exists — FSD forbids it. Use shared/lib, shared/ui, ` +
            `or place components inside a feature.`,
        );
      }
    }
    expect(
      offenders,
      `FSD anti-pattern #11 — forbidden top-level packages:\n` +
        offenders.map((m) => `  - ${m}`).join("\n"),
    ).toEqual([]);
  });

  it("every entities/<x>/ and features/<x>/ slice exposes index.ts", () => {
    const offenders: string[] = [];

    for (const sliceParent of ["entities", "features"]) {
      for (const subDir of iterSrcFiles(`${sliceParent}/*/`)) {
        // iterSrcFiles with a trailing / returns subdirectories.
        const barrel = resolve(subDir, "index.ts");
        if (!existsSync(barrel)) {
          offenders.push(
            `${relToFrontend(subDir)} has no index.ts — every slice must ` +
              `expose its public API via a barrel.`,
          );
        }
      }
      // Fallback: glob returns nothing for directories on some platforms.
      // Walk via globSync of any file inside, then derive parent dir.
      const seen = new Set<string>();
      for (const inner of iterSrcFiles(`${sliceParent}/*/**/*.{ts,tsx}`)) {
        const sliceDir = dirname(inner).split(`/${sliceParent}/`)[1]?.split("/")[0];
        if (!sliceDir || seen.has(sliceDir)) continue;
        seen.add(sliceDir);
        const barrel = resolve(SRC_ROOT, sliceParent, sliceDir, "index.ts");
        if (!existsSync(barrel)) {
          offenders.push(
            `src/${sliceParent}/${sliceDir} has no index.ts — every slice must ` +
              `expose its public API via a barrel.`,
          );
        }
      }
    }

    expect(
      offenders,
      `FSD rule #5 violation — slice without a barrel:\n` +
        offenders.map((m) => `  - ${m}`).join("\n"),
    ).toEqual([...new Set([])]);
  });

  it("no JSDoc close-sequence inside comments breaks esbuild", () => {
    // The gotcha: a "*/" inside a "/** ... */" block closes the comment.
    // We detect it by scanning for the literal close sequence inside what
    // appears to be an open JSDoc block.
    const offenders: string[] = [];
    const closeSequence = "*" + "/"; // literal */ — split to avoid this very check

    for (const abs of iterSrcFiles("**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      const text = readUtf8(abs);
      // Find every JSDoc opener and inspect its body for an inner close sequence.
      const matches = text.matchAll(/\/\*\*[\s\S]*?\*\//g);
      for (const m of matches) {
        const body = m[0].slice(3, -2); // strip leading /** and trailing */
        if (body.includes(closeSequence)) {
          const line = text.slice(0, m.index ?? 0).split("\n").length;
          offenders.push(`${rel}:${line} JSDoc contains an inner "${closeSequence}" sequence`);
        }
      }
    }

    expect(
      offenders,
      `FSD anti-pattern #12 — "${closeSequence}" inside a JSDoc block ` +
        `closes the comment early and breaks esbuild:\n` +
        offenders.map((m) => `  - ${m}`).join("\n") +
        `\nUse <x> placeholders in glob examples instead.`,
    ).toEqual([]);
  });
});

function stripCommentsAndStrings(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "")
    .replace(/(["'`])(?:\\.|(?!\1).)*\1/g, '""');
}
