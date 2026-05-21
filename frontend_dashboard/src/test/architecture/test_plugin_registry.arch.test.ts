/**
 * Test #19 — plugin registry generation invariants.
 *
 * Background
 * ----------
 * `scripts/plugins-sync.ts` scans `src/plugins/<id>/plugin.yaml` and emits
 * `src/app/plugin-registry.generated.ts`, which exports a `PLUGINS[]` array
 * consumed by `pages/Dashboard.tsx`. Every entry in that array carries a
 * `Page: lazy(() => import("@plugins/<id>/frontend"))`. If a plugin without a
 * `frontend:` block (backend-only — e.g. `system_map` exposes only
 * `/api/system-map/graph` and its UI lives in a separate Vite container at
 * `system_explorer/`) leaks into the registry, the Vite import-analysis pass
 * fails with a cryptic:
 *
 *   `Failed to resolve import "@plugins/<id>/frontend"`
 *
 * Two invariants enforce the contract:
 *
 *   #19a  Every plugin id in `PLUGINS[]` MUST declare a `frontend:` block in
 *         its manifest AND have a frontend entry directory on disk.
 *
 *   #19b  Every plugin manifest that DOES declare a `frontend:` block (with
 *         the entry existing on disk) MUST appear in `PLUGINS[]`. This catches
 *         the reverse bug: an over-aggressive filter that drops valid plugins.
 *
 * What this test does NOT enforce
 * --------------------------------
 * It does not verify that the registry's contents (sidebar/sections labels,
 * icons) match the manifest verbatim — that is the script's job and is unit-
 * tested elsewhere. This test only enforces the SET of ids in `PLUGINS[]`.
 *
 * If this test fails, the fix is almost always:
 *   - run `npm run plugins:sync` to regenerate, OR
 *   - inspect why a backend-only plugin lost its `frontend:` exclusion in the
 *     sync script (regression in `scripts/plugins-sync.ts`).
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { SRC_ROOT } from "./helpers";

type Manifest = {
  id?: string;
  frontend?: {
    entry?: string;
  };
};

const PLUGINS_DIR = join(SRC_ROOT, "plugins");
const REGISTRY_FILE = join(SRC_ROOT, "app", "plugin-registry.generated.ts");

/**
 * Read every `src/plugins/<id>/plugin.yaml` and return `{ id, manifest }`
 * pairs. Skips directories without a manifest, but does NOT skip backend-only
 * plugins — the test relies on inspecting the `frontend` block presence.
 */
function loadAllManifests(): Array<{ id: string; manifest: Manifest }> {
  const out: Array<{ id: string; manifest: Manifest }> = [];
  const { readdirSync } = require("node:fs") as typeof import("node:fs");

  for (const entry of readdirSync(PLUGINS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
    const manifestPath = join(PLUGINS_DIR, entry.name, "plugin.yaml");
    if (!existsSync(manifestPath)) continue;
    const raw = readFileSync(manifestPath, "utf-8");
    const manifest = parse(raw) as Manifest;
    if (!manifest?.id) continue;
    out.push({ id: entry.name, manifest });
  }
  return out;
}

/**
 * Extract plugin ids that appear in `PLUGINS[]` of the generated registry by
 * regex-scanning the file. We intentionally do not import the module: it
 * triggers `lazy(() => import(...))` references that would themselves fail to
 * resolve, defeating the purpose of the test.
 */
function readRegistryIds(): string[] {
  if (!existsSync(REGISTRY_FILE)) {
    throw new Error(
      `Registry not found at ${REGISTRY_FILE}. Run \`npm run plugins:sync\` first.`,
    );
  }
  const text = readFileSync(REGISTRY_FILE, "utf-8");
  const ids: string[] = [];
  // Match the canonical generator output: `id: "agents_admin",`
  const re = /^\s{2,4}id:\s*"([a-z][a-z0-9_]*)",\s*$/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    ids.push(m[1]);
  }
  return ids;
}

function hasFrontendEntryOnDisk(pluginId: string, manifest: Manifest): boolean {
  if (!manifest.frontend) return false;
  const entryRel = manifest.frontend.entry ?? "./frontend";
  const entryAbs = resolve(PLUGINS_DIR, pluginId, entryRel);
  return existsSync(entryAbs);
}

describe("R-PLUGIN-REGISTRY — registry only includes plugins with frontend", () => {
  it("every plugin in PLUGINS[] has a `frontend:` block + entry on disk", () => {
    const registryIds = readRegistryIds();
    const manifests = new Map(
      loadAllManifests().map((m) => [m.id, m.manifest] as const),
    );

    const offenders: string[] = [];
    for (const id of registryIds) {
      const manifest = manifests.get(id);
      if (!manifest) {
        offenders.push(
          `"${id}" — appears in PLUGINS[] but no manifest at ` +
            `src/plugins/${id}/plugin.yaml`,
        );
        continue;
      }
      if (!manifest.frontend) {
        offenders.push(
          `"${id}" — appears in PLUGINS[] but manifest declares no \`frontend:\` ` +
            `block (backend-only plugins must not be in the dashboard registry)`,
        );
        continue;
      }
      if (!hasFrontendEntryOnDisk(id, manifest)) {
        const entryRel = manifest.frontend.entry ?? "./frontend";
        offenders.push(
          `"${id}" — appears in PLUGINS[] but \`frontend.entry\` "${entryRel}" ` +
            `does not exist on disk`,
        );
      }
    }

    expect(
      offenders,
      `Plugin registry contains entries that will fail Vite import-analysis:\n` +
        offenders.map((m) => `  - ${m}`).join("\n") +
        `\n\nRun \`npm run plugins:sync\` to regenerate the registry. ` +
        `If a backend-only plugin is leaking in, audit ` +
        `\`scripts/plugins-sync.ts\` (see Test #19 docstring).`,
    ).toEqual([]);
  });

  it("every plugin with a valid `frontend:` block appears in PLUGINS[]", () => {
    const registryIds = new Set(readRegistryIds());
    const manifests = loadAllManifests();

    const missing: string[] = [];
    for (const { id, manifest } of manifests) {
      if (!manifest.frontend) continue; // backend-only — correctly excluded
      if (!hasFrontendEntryOnDisk(id, manifest)) continue; // broken entry — correctly excluded
      if (!registryIds.has(id)) {
        missing.push(id);
      }
    }

    expect(
      missing,
      `Plugins declare a \`frontend:\` block with a valid entry on disk but ` +
        `are absent from PLUGINS[]:\n` +
        missing.map((id) => `  - ${id}`).join("\n") +
        `\n\nThis usually means the generated registry is stale. Run ` +
        `\`npm run plugins:sync\` to regenerate.`,
    ).toEqual([]);
  });
});
