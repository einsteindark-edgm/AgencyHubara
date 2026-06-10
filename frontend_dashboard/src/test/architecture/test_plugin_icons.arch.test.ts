/**
 * P-12 (PLUGIN_CONTRACT.md P-ICON) — every icon a plugin manifest references
 * exists in the central Icon registry (src/shared/ui/Icon.tsx).
 *
 * Why
 * ---
 * `Toolbar.resolveIcon()` does `Icon[name]` and falls back to `Icon.bot` +
 * `console.warn` when the name is absent. A plugin shipping an unregistered
 * glyph renders the WRONG icon SILENTLY (audit F4/F11 — PLUGIN_ISOLATION_AUDIT.md).
 * This test turns that into a CI failure. (`test_plugin_registry` only checks the
 * SET of plugin ids, explicitly NOT icons — see its docstring.)
 *
 * Manifest icons appear under several contribution points:
 *   frontend.contributes.sidebar[].icon · frontend.contributes.sections[].icon ·
 *   agent.workers[].dashboard.icon · agent.workers[].dashboard.capabilities[].icon
 * We collect every value found under an `icon` key recursively, so new
 * icon-bearing contribution points are covered automatically.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { Icon } from "@/shared/ui/Icon";
// F7: los plugins pueden CONTRIBUIR glifos propios (frontend/icons.tsx) que
// plugins-sync agrega al registry generado — el set efectivo del Toolbar es
// base ∪ contribuciones. Requiere `npm run plugins:sync` previo (CI lo corre).
import { PLUGIN_ICONS } from "@/app/plugin-registry.generated";

import { SRC_ROOT } from "./helpers";

const PLUGINS_DIR = join(SRC_ROOT, "plugins");

/** Recursively collect every string value found under an `icon` key. */
function collectIcons(node: unknown, out: Set<string>): void {
  if (Array.isArray(node)) {
    for (const item of node) collectIcons(item, out);
  } else if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (key === "icon" && typeof value === "string") out.add(value);
      else collectIcons(value, out);
    }
  }
}

function manifestIcons(): Array<{ plugin: string; icon: string }> {
  const out: Array<{ plugin: string; icon: string }> = [];
  for (const entry of readdirSync(PLUGINS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
    const manifestPath = join(PLUGINS_DIR, entry.name, "plugin.yaml");
    if (!existsSync(manifestPath)) continue;
    const manifest = parse(readFileSync(manifestPath, "utf-8")) as unknown;
    const icons = new Set<string>();
    collectIcons(manifest, icons);
    for (const icon of icons) out.push({ plugin: entry.name, icon });
  }
  return out;
}

describe("R-PLUGIN-ICONS — manifest icons resolve in the Icon registry", () => {
  it("every icon declared in a plugin manifest exists in Icon ∪ PLUGIN_ICONS", () => {
    const registry = new Set([...Object.keys(Icon), ...Object.keys(PLUGIN_ICONS)]);
    const offenders = manifestIcons()
      .filter(({ icon }) => !registry.has(icon))
      .map(({ plugin, icon }) => `${plugin}: "${icon}"`);

    expect(
      offenders,
      `Plugin manifests reference icons missing from the effective registry ` +
        `(base Icon.tsx + plugin contributions) — Toolbar renders the 'bot' ` +
        `fallback silently (PLUGIN_CONTRACT.md P-ICON):\n` +
        offenders.map((m) => `  - ${m}`).join("\n") +
        `\n\nFix (F7): que el plugin TRAIGA su glifo en frontend/icons.tsx ` +
        `(export const icons = { nombre: Componente }) y corras ` +
        `\`npm run plugins:sync\` — cero ediciones a Icon.tsx (INV-1).`,
    ).toEqual([]);
  });
});
