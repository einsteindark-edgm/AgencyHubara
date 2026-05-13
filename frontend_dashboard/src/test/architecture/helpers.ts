/**
 * Helpers and allow-lists for the architecture test suite.
 *
 * Mirrors the role of `tests/architecture/conftest.py` in the Python side:
 *   - resolves SRC_ROOT / REPO_ROOT from this file's path,
 *   - centralizes path globs per FSD layer,
 *   - centralizes the protected-paths set used by the meta-gate,
 *   - exposes file iteration + simple AST/regex utilities so each test stays
 *     focused on its rule.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { globSync } from "glob";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// frontend_dashboard/src/test/architecture/helpers.ts → 3x parents = frontend_dashboard/
export const FRONTEND_ROOT: string = resolve(__dirname, "..", "..", "..");
export const SRC_ROOT: string = resolve(FRONTEND_ROOT, "src");
export const REPO_ROOT: string = resolve(FRONTEND_ROOT, "..");

// ----------------------------------------------------------------------------
// FSD layer globs (relative to SRC_ROOT). Use `iterFiles(glob)` to walk them.
// ----------------------------------------------------------------------------

export const LAYER_GLOBS = {
  app: "app/**/*.{ts,tsx}",
  pages: "pages/**/*.{ts,tsx}",
  features: "features/**/*.{ts,tsx}",
  entities: "entities/**/*.{ts,tsx}",
  shared: "shared/**/*.{ts,tsx}",
} as const;

export const ENTITY_API_GLOB = "entities/*/api.{ts,tsx}";
export const SHARED_API_GLOB = "shared/api/**/*.{ts,tsx}";

// Tests co-located inside slices follow the FSD convention; the architecture
// suite never blames them for "deep imports" — they probe internals legitimately.
export const TEST_FILE_GLOB = "**/*.test.{ts,tsx}";

// ----------------------------------------------------------------------------
// Allow-lists — document every exemption with an owner reason.
// ----------------------------------------------------------------------------

/** Files allowed to call `fetch(...)` directly. */
export const FETCH_CALL_ALLOWLIST: ReadonlySet<string> = new Set([
  "src/shared/api/client.ts", // the wrapper itself
  // (entities/*/api.ts use apiClient, not fetch directly — they're allowed
  // anyway because they're the only consumers of fetch beyond the wrapper)
]);

/** Files allowed to read `import.meta.env.*`. */
export const ENV_READ_ALLOWLIST: ReadonlySet<string> = new Set([
  "src/shared/config/env.ts",
]);

/** Files allowed to embed hardcoded `http://` / `https://` literals. */
export const HARDCODED_URL_ALLOWLIST: ReadonlySet<string> = new Set([
  "src/index.css", // Google Fonts CDN URL
]);

/** Top-level packages under src/ that FSD forbids — anti-pattern #11. */
export const FORBIDDEN_TOP_LEVEL_FOLDERS: readonly string[] = [
  "components",
  "utils",
  "lib",
  "helpers",
];

/**
 * .css files allowed under src/ besides the canonical index.css.
 * Each entry is documented debt — reducing this set is a refactor goal.
 */
export const CSS_FILE_ALLOWLIST: ReadonlySet<string> = new Set([
  "src/index.css",
  // Pre-existing migration debt: legacy panel styles still referenced by
  // pages/Dashboard.tsx. Replace with Tailwind utilities + delete the file.
  "src/pages/Dashboard.legacy.css",
]);

/** Architecture-protected paths (Capa 3 — meta-gate). Relative to REPO_ROOT. */
export const ARCHITECTURE_PROTECTED_PREFIXES: readonly string[] = [
  // Tests + dependency-cruiser contract — la arquitectura FSD misma.
  "frontend_dashboard/src/test/architecture/",
  "frontend_dashboard/.dependency-cruiser.cjs",
  // Workflows de Archon y skills que orquestan el pipeline. Si un AI
  // implementer pudiera editar el pipeline YAML o el SKILL.md mismo, podría
  // deshabilitar el gate determinista o borrar las reglas §11. Protegerlos
  // cierra ese hueco — Capa 3 cubre ahora también el "framework" que evalúa.
  ".archon/workflows/",
  ".claude/skills/frontend-",
];

// ----------------------------------------------------------------------------
// File iteration utilities.
// ----------------------------------------------------------------------------

/** Returns absolute paths of files matching `glob` under SRC_ROOT. */
export function iterSrcFiles(glob: string): string[] {
  return globSync(glob, {
    cwd: SRC_ROOT,
    absolute: true,
    nodir: true,
  }).sort();
}

/** Path string relative to FRONTEND_ROOT, slash-style. */
export function relToFrontend(absPath: string): string {
  return absPath.startsWith(FRONTEND_ROOT)
    ? absPath.slice(FRONTEND_ROOT.length + 1).replace(/\\/g, "/")
    : absPath;
}

/** Read a file as UTF-8. Cached lazily — the suite re-reads small files often. */
const _readCache = new Map<string, string>();
export function readUtf8(absPath: string): string {
  const cached = _readCache.get(absPath);
  if (cached !== undefined) return cached;
  const text = readFileSync(absPath, "utf-8");
  _readCache.set(absPath, text);
  return text;
}

/** True if `path` (relative to repo root) matches a protected prefix. */
export function isProtectedPath(repoRelPath: string): boolean {
  return ARCHITECTURE_PROTECTED_PREFIXES.some((p) => repoRelPath.startsWith(p));
}

// ----------------------------------------------------------------------------
// Git helpers — used by the meta-gate test. Pure-ish (no side effects).
// ----------------------------------------------------------------------------

export function runGit(...args: string[]): { code: number; stdout: string } {
  try {
    const stdout = execFileSync("git", args, {
      cwd: REPO_ROOT,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return { code: 0, stdout };
  } catch (err) {
    const e = err as { status?: number; stdout?: string };
    return { code: e.status ?? 1, stdout: e.stdout ?? "" };
  }
}

export function isGitRepo(): boolean {
  return existsSync(resolve(REPO_ROOT, ".git"));
}
