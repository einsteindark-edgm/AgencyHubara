/**
 * Meta-test #18 — guard the architecture suite against silent rewrites.
 *
 * Why this exists
 * ---------------
 * The architectural gate is only as strong as the trust in its tests. If a
 * coding agent (human or AI) could edit `src/test/architecture/` or
 * `.dependency-cruiser.cjs` in the same PR that adds a feature, the gate is
 * just a sticker. This meta-test enforces that those files are CHANGED ONLY by
 * an explicitly-approved architecture PR.
 *
 *   - Diffs the current worktree against `origin/main` (fallback: `main`).
 *   - Fails if any path under `ARCHITECTURE_PROTECTED_PREFIXES` is modified.
 *   - Allows bypass via the environment variable `ARCH_CHANGE_APPROVED=1`.
 *
 * Bypass policy
 * -------------
 * `ARCH_CHANGE_APPROVED=1` is the ONLY way to allow modifications:
 *
 *   - In CI, it is set by the architecture-change CI job ONLY when the PR
 *     carries the `architecture-change` label AND a CODEOWNERS-approved review.
 *   - Locally, set it manually when iterating on a deliberate architecture
 *     change (e.g. landing an ADR). Treat each manual set as a conscious act.
 *
 * Skip conditions (informative skip, not a fail):
 *   - Not running inside a git repository.
 *   - Neither `origin/main` nor `main` ref is available.
 *   - HEAD is checked out on the `main` branch (nothing to diff against).
 *
 * Honest caveat
 * -------------
 * This test runs the WORKING-TREE version of itself. A sufficiently capable
 * agent can disable it by editing this file in the same PR — though doing so
 * would itself show up in the diff and is visible to reviewers. For
 * tamper-resistant enforcement, pair this test with:
 *
 *   - Branch protection + CODEOWNERS on `src/test/architecture` and
 *     `.dependency-cruiser.cjs` (Capa 4).
 *   - A CI job that runs `npm run test:arch` from a clean checkout of
 *     `origin/main` HEAD, not from the PR branch (out-of-band enforcement).
 *   - Optional: pinned-checksum of each protected file, verified by CI (Capa 5).
 */
import { describe, it } from "vitest";

import {
  ARCHITECTURE_PROTECTED_PREFIXES,
  isGitRepo,
  isProtectedPath,
  runGit,
} from "./helpers";

const BYPASS_ENV_VAR = "ARCH_CHANGE_APPROVED";
const BASE_REF_CANDIDATES = ["origin/main", "main"] as const;

function pickBaseRef(): string | null {
  for (const ref of BASE_REF_CANDIDATES) {
    const { code } = runGit("rev-parse", "--verify", "--quiet", ref);
    if (code === 0) return ref;
  }
  return null;
}

/**
 * Resolve the merge-base between `base` and HEAD so the gate diffs with
 * PR-diff semantics (`git diff base...HEAD`) instead of a two-dot diff.
 *
 * A two-dot `git diff origin/main` flags every file that `main` ADVANCED after
 * the branch forked — a false positive that breaks in-flight HU branches each
 * time the harness lands a fix on `main` (e.g. an edit to
 * `.archon/workflows/*.yaml`). The merge-base reports ONLY what the branch
 * itself introduced. Falls back to `base` if merge-base is unavailable
 * (unrelated histories) — never auto-disables the protection.
 * See docs/adr/2026-05-29-meta-gate-merge-base.md.
 */
function mergeBaseWith(base: string): string {
  const { code, stdout } = runGit("merge-base", base, "HEAD");
  if (code !== 0) return base;
  const sha = stdout.trim();
  return sha === "" ? base : sha;
}

function currentBranch(): string | null {
  const { code, stdout } = runGit("rev-parse", "--abbrev-ref", "HEAD");
  if (code !== 0) return null;
  const name = stdout.trim();
  return name === "" ? null : name;
}

function changedFilesVs(base: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];

  const tracked = runGit("diff", "--name-only", base, "--");
  if (tracked.code === 0) {
    for (const raw of tracked.stdout.split("\n")) {
      const line = raw.trim();
      if (line && !seen.has(line)) {
        seen.add(line);
        out.push(line);
      }
    }
  }

  const untracked = runGit("ls-files", "--others", "--exclude-standard");
  if (untracked.code === 0) {
    for (const raw of untracked.stdout.split("\n")) {
      const line = raw.trim();
      if (line && !seen.has(line)) {
        seen.add(line);
        out.push(line);
      }
    }
  }

  return out;
}

describe("Meta-gate — architecture files unchanged vs main", () => {
  it("protected files unchanged vs origin/main (or skip with reason)", ({ skip }) => {
    if (process.env[BYPASS_ENV_VAR] === "1") {
      skip(
        `${BYPASS_ENV_VAR}=1 — bypass acknowledged. In CI this is set only ` +
          `when the PR carries the architecture-change label and a CODEOWNERS-` +
          `approved review. Locally, you set it intentionally when working on ` +
          `a deliberate architecture change.`,
      );
      return;
    }
    if (!isGitRepo()) {
      skip("Not inside a git repository — meta-gate does not apply.");
      return;
    }
    const base = pickBaseRef();
    if (base === null) {
      skip(
        "No `origin/main` or `main` ref available locally. " +
          "Run `git fetch origin main` if you want the meta-gate to engage.",
      );
      return;
    }
    const branch = currentBranch();
    if (branch === "main") {
      skip("HEAD is on `main` — there is no PR to compare against.");
      return;
    }

    // PR-diff semantics: compare against the merge-base, not `base` directly,
    // so files that `main` advanced in parallel are not misattributed to this
    // branch. See docs/adr/2026-05-29-meta-gate-merge-base.md.
    const diffBase = mergeBaseWith(base);
    const changed = changedFilesVs(diffBase);
    const offenders = changed.filter(isProtectedPath).sort();
    if (offenders.length > 0) {
      throw new Error(
        `Meta-gate violation — architecture-protected files modified vs ` +
          `\`${base}\` without ${BYPASS_ENV_VAR}=1:\n` +
          offenders.map((p) => `  - ${p}`).join("\n") +
          `\n\nProtected prefixes:\n` +
          ARCHITECTURE_PROTECTED_PREFIXES.map((p) => `  - ${p}`).join("\n") +
          `\n\nThese files encode the FSD architectural contract. Modifying ` +
          `them requires:\n` +
          `  1. An ADR documenting the proposed change.\n` +
          `  2. A separate PR carrying the \`architecture-change\` label.\n` +
          `  3. A CODEOWNERS-approved review.\n\n` +
          `If THIS is the architecture-change PR itself: set ` +
          `${BYPASS_ENV_VAR}=1 (CI does it automatically when the label and ` +
          `review are present).\n` +
          `If you are an AI coding assistant: STOP. Surface this in the task ` +
          `result as \`status: blocked, reason: requires_planner_update\`. ` +
          `Do not edit the protected files to make the gate pass.`,
      );
    }
  });
});
