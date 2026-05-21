/**
 * plugins-sync — escanea `src/plugins/<id>/plugin.yaml` y genera
 * `src/app/plugin-registry.generated.ts` con los entries habilitados.
 *
 * Filtrado por `process.env.ENABLED_PLUGINS` (csv):
 *   - Si está vacío o unset → cargar TODOS los plugins descubiertos.
 *   - Si está seteado       → cargar solo los listados (intersección con
 *                              los que tienen manifest válido).
 *
 * Convención: el archivo generado se ignora vía `.gitignore`. Lo regenera
 * automáticamente `npm run predev` y `npm run prebuild` (ver `package.json`).
 *
 * Validaciones que el sync aplica (en orden):
 *
 *   1. `id` matchea el regex del schema (`^[a-z][a-z0-9_]*$`). Importante:
 *      el id es también un segmento de path Python.
 *   2. `id` == nombre del directorio (no se permite que un manifest declare
 *      `id: foo` viviendo en `src/plugins/bar/`).
 *   3. El plugin declara bloque `frontend:` en su manifest. Plugins sin
 *      bloque `frontend:` son backend-only (ej. `system_map` expone solo
 *      API REST; su UI vive en un container separado). NO deben aparecer
 *      en el registry del dashboard — un `lazy(() => import("@plugins/<id>/frontend"))`
 *      sin `frontend/` en disco rompe el bundler con un error críptico.
 *   4. `frontend.entry` (default `./frontend`) existe en disco — defensa
 *      adicional para cuando el bloque `frontend:` sí está pero el entry
 *      apunta a una ruta inexistente.
 *   5. Colisión de `section.key` entre plugins — warning (el shell muestra
 *      la última, lo que típicamente es un bug).
 *
 * Diseño explícito:
 *   - Cero dependencias en runtime de la app — el script solo se ejecuta
 *     en build/dev.
 *   - El registry es un módulo TS importable desde `pages/Dashboard.tsx`
 *     y desde cualquier shell que necesite enumerar plugins.
 *   - El `Page` se carga con `lazy(() => import("@plugins/<id>/frontend"))`
 *     para code-splitting; el bundler decide el chunking.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(__dirname, "..");
const PLUGINS_DIR = join(FRONTEND_ROOT, "src", "plugins");
const OUT_FILE = join(FRONTEND_ROOT, "src", "app", "plugin-registry.generated.ts");

// Espejo del pattern declarado en `_schema/plugin.schema.yaml`. Single-source-
// of-truth viviría en el schema pero compilar JSON Schema en runtime es overkill
// para esta validación. Si cambia uno, cambiar el otro (y agregar un test).
const PLUGIN_ID_PATTERN = /^[a-z][a-z0-9_]*$/;

type SidebarEntry = {
  route: string;
  label: string;
  icon?: string;
  badge_query?: string;
};

type SectionEntry = {
  key: string;
  label: string;
  order?: number;
  icon?: string;
};

type DashboardWidget = {
  id: string;
  position: string;
};

type Manifest = {
  id?: string;
  version?: string;
  display_name?: string;
  frontend?: {
    entry?: string;
    contributes?: {
      sidebar?: SidebarEntry[];
      sections?: SectionEntry[];
      dashboard_widgets?: DashboardWidget[];
    };
  };
};

type RegistryEntry = {
  id: string;
  displayName: string;
  sidebar: SidebarEntry[];
  sections: SectionEntry[];
  dashboardWidgets: DashboardWidget[];
};

/** Loguea con prefijo común — facilita grep en la salida de CI. */
function logWarn(msg: string): void {
  console.warn(`[plugins-sync] ${msg}`);
}

function logInfo(msg: string): void {
  console.log(`[plugins-sync] ${msg}`);
}

function discoverManifests(): Map<string, Manifest> {
  const found = new Map<string, Manifest>();

  if (!existsSync(PLUGINS_DIR)) {
    return found;
  }

  for (const entry of readdirSync(PLUGINS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;

    const manifestPath = join(PLUGINS_DIR, entry.name, "plugin.yaml");
    if (!existsSync(manifestPath)) continue;

    const raw = readFileSync(manifestPath, "utf-8");
    let manifest: Manifest;
    try {
      manifest = parse(raw) as Manifest;
    } catch (err) {
      logWarn(`skip ${entry.name}: invalid YAML — ${(err as Error).message}`);
      continue;
    }

    if (!manifest?.id || !manifest?.version) {
      logWarn(`skip ${entry.name}: missing id or version`);
      continue;
    }
    if (!PLUGIN_ID_PATTERN.test(manifest.id)) {
      logWarn(
        `skip ${entry.name}: id "${manifest.id}" violates pattern ${PLUGIN_ID_PATTERN} ` +
          `(must start with [a-z], contain only [a-z0-9_]; ids are used as Python path segments)`,
      );
      continue;
    }
    if (manifest.id !== entry.name) {
      logWarn(
        `skip ${entry.name}: manifest id (${manifest.id}) != directory name`,
      );
      continue;
    }

    // Plugins backend-only (sin bloque `frontend:` en el manifest) NO entran
    // al registry del dashboard. Caso paradigmático: `system_map` expone solo
    // `/api/system-map/graph` y su UI vive en un container Vite separado
    // (`system_explorer/`). Incluirlos generaría un `import("@plugins/<id>/frontend")`
    // que el bundler no puede resolver. Info-log para que un dev pueda confirmar
    // por qué el plugin no aparece en sidebar/sections.
    if (!manifest.frontend) {
      logInfo(
        `skip ${entry.name}: backend-only (no frontend block in manifest) — ` +
          `does not contribute to the dashboard registry`,
      );
      continue;
    }

    // Bloque `frontend:` declarado → el entry debe existir en disco. Sin esto,
    // `lazy(() => import("@plugins/<id>/frontend"))` falla en runtime con un
    // error críptico del bundler. Mejor catch al sync time.
    const entryRel = manifest.frontend.entry ?? "./frontend";
    const entryAbs = resolve(PLUGINS_DIR, entry.name, entryRel);
    if (!existsSync(entryAbs)) {
      logWarn(
        `skip ${entry.name}: frontend.entry "${entryRel}" does not exist ` +
          `(expected at ${entryAbs.replace(FRONTEND_ROOT + "/", "")})`,
      );
      continue;
    }

    found.set(entry.name, manifest);
  }

  return found;
}

function filterEnabled(found: Map<string, Manifest>): Manifest[] {
  const envValue = process.env.ENABLED_PLUGINS?.trim();
  if (!envValue) {
    // Sin filtro → cargar todos (modo dev convenient).
    return [...found.values()].sort((a, b) => a.id!.localeCompare(b.id!));
  }
  const enabled = new Set(envValue.split(",").map((s) => s.trim()).filter(Boolean));
  const out: Manifest[] = [];
  for (const id of [...enabled].sort()) {
    const manifest = found.get(id);
    if (!manifest) {
      logWarn(`ENABLED_PLUGINS lists "${id}" but no manifest found`);
      continue;
    }
    out.push(manifest);
  }
  return out;
}

function buildEntry(manifest: Manifest): RegistryEntry {
  const contributes = manifest.frontend?.contributes ?? {};
  return {
    id: manifest.id!,
    displayName: manifest.display_name ?? manifest.id!,
    sidebar: contributes.sidebar ?? [],
    sections: (contributes.sections ?? []).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)),
    dashboardWidgets: contributes.dashboard_widgets ?? [],
  };
}

/**
 * Detecta plugins distintos que contribuyen la misma `section.key`. El shell
 * mantiene un map `key → Page` que pisa el anterior; típicamente esto es un
 * bug del manifest (alguien copió una key sin cambiarla). Warning, no error
 * — para no romper builds cuando dos plugins genuinamente colisionan durante
 * una migración (caso transient).
 */
function warnSectionKeyCollisions(entries: RegistryEntry[]): void {
  const seen = new Map<string, string>(); // key → first plugin id
  for (const e of entries) {
    for (const s of e.sections) {
      const prev = seen.get(s.key);
      if (prev && prev !== e.id) {
        logWarn(
          `section.key "${s.key}" is contributed by both "${prev}" and "${e.id}" — ` +
            `the shell will render only the last one. Use distinct keys.`,
        );
      } else {
        seen.set(s.key, e.id);
      }
    }
  }
}

function renderRegistry(entries: RegistryEntry[]): string {
  // `lazy` solo se importa si hay al menos un entry — el tsconfig tiene
  // `noUnusedLocals: true` y rechazaría un import sin uso.
  const headerComment = `/**
 * AUTO-GENERATED by frontend_dashboard/scripts/plugins-sync.ts — DO NOT EDIT.
 * Regenerate: \`npm run plugins:sync\`.
 *
 * Source of truth: \`src/plugins/<id>/plugin.yaml\`.
 * Filter: \`process.env.ENABLED_PLUGINS\` (csv; empty = all).
 *
 * Generated: ${new Date().toISOString()}
 * Plugins: ${entries.length === 0 ? "(none)" : entries.map((e) => e.id).join(", ")}
 */
`;

  const sharedTypes = `
export type SidebarEntry = {
  route: string;
  label: string;
  icon?: string;
  badge_query?: string;
};

export type SectionEntry = {
  key: string;
  label: string;
  order?: number;
  icon?: string;
};

export type DashboardWidget = {
  id: string;
  position: string;
};
`;

  if (entries.length === 0) {
    // Sin plugins: emitimos un array vacío con tipo opaco. Cero imports de
    // React → no se rompe `noUnusedLocals` y la app puede importar PLUGINS
    // de un proyecto recién creado sin friction.
    return (
      headerComment +
      sharedTypes +
      `
export type PluginEntry = {
  id: string;
  displayName: string;
  sidebar: SidebarEntry[];
  sections: SectionEntry[];
  dashboardWidgets: DashboardWidget[];
  Page: () => null;
};

export const PLUGINS: PluginEntry[] = [];
`
    );
  }

  // El `Page` del registry es deliberadamente laxo en tipos (`ComponentType<any>`)
  // porque cada plugin define su propia firma de props. El shell que renderiza
  // `<entry.Page {...props} />` conoce las props específicas del plugin que
  // está mostrando. Formalizar con generics queda pendiente.
  const importBlock = `import { lazy, type ComponentType, type LazyExoticComponent } from "react";\n`;
  const fullType = `
export type PluginEntry = {
  id: string;
  displayName: string;
  sidebar: SidebarEntry[];
  sections: SectionEntry[];
  dashboardWidgets: DashboardWidget[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  Page: LazyExoticComponent<ComponentType<any>>;
};

export const PLUGINS: PluginEntry[] = [
`;

  // JSON pretty (2-space indent) — git diffs legibles cuando un plugin cambia
  // una sidebar entry. El registry está gitignored, pero algunos devs lo
  // inspeccionan manualmente para debug.
  const indent = (s: string, depth: number): string =>
    s
      .split("\n")
      .map((line, i) => (i === 0 ? line : " ".repeat(depth) + line))
      .join("\n");

  const body = entries
    .map((e) => {
      const importExpr = `lazy(() => import("@plugins/${e.id}/frontend"))`;
      return `  {
    id: ${JSON.stringify(e.id)},
    displayName: ${JSON.stringify(e.displayName)},
    sidebar: ${indent(JSON.stringify(e.sidebar, null, 2), 4)},
    sections: ${indent(JSON.stringify(e.sections, null, 2), 4)},
    dashboardWidgets: ${indent(JSON.stringify(e.dashboardWidgets, null, 2), 4)},
    Page: ${importExpr},
  },`;
    })
    .join("\n");

  const footer = `\n];\n`;

  return headerComment + importBlock + sharedTypes + fullType + body + footer;
}

function main() {
  const found = discoverManifests();
  const enabled = filterEnabled(found);
  const entries = enabled.map(buildEntry);

  warnSectionKeyCollisions(entries);

  const outDir = dirname(OUT_FILE);
  if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });

  writeFileSync(OUT_FILE, renderRegistry(entries), "utf-8");
  logInfo(
    `generated ${OUT_FILE.replace(FRONTEND_ROOT + "/", "")} ` +
      `with ${entries.length} plugin(s): ${entries.map((e) => e.id).join(", ") || "(empty)"}`,
  );
}

main();
