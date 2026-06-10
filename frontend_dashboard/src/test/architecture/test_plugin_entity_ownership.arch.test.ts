/**
 * P-22 + P-23 — detección REAL de consumo cross-plugin (F3 del plan fable).
 *
 * Por qué existe (hallazgo N-3 de PLUGIN_ISOLATION_AUDIT_fable.md): el gate
 * P-9 del backend grepea texto `/api/<otro>/` bajo plugins/ — hoy sus únicos
 * matches son COMENTARIOS JSDoc, y es ciego al canal real: un plugin importa
 * una entity central cuya api.ts llama al API de otro plugin (lavado). Estos
 * dos tests miden el canal real:
 *
 *   P-22 — un archivo bajo plugins/<X>/ no importa `@/entities/<e>` cuya
 *          owner (src/entities/OWNERS.yaml) sea otro plugin. El día que un
 *          consumo cross-plugin sea legítimo, se declara con `consumes:` en
 *          el manifest (cast server-side, PLUGIN_CONTRACT.md §5.3) y la
 *          entity local del consumidor reemplaza el import ajeno.
 *   P-23 — todo literal `/api/...` en CÓDIGO (comentarios excluidos) bajo
 *          entities/<e> o plugins/<X> resuelve, por longest-prefix contra los
 *          prefixes declarados en los manifests, al plugin OWNER del archivo.
 *
 * Deuda conocida: los ofensores actuales están en EXPECTED_* abajo con su
 * fase de resolución. La igualdad es EXACTA en ambas direcciones — un ofensor
 * nuevo rompe CI, y arreglar uno OBLIGA a sacarlo de la lista (este archivo
 * es PROTECTED: el shrink queda visible y requiere label architecture-change).
 */
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { describe, expect, test } from "vitest";

import { REPO_ROOT } from "./helpers";

const FRONTEND_ROOT = join(REPO_ROOT, "frontend_dashboard");
const SRC = join(FRONTEND_ROOT, "src");
const ENTITIES_DIR = join(SRC, "entities");
const PLUGINS_DIR = join(SRC, "plugins");

// ── Deuda conocida (igualdad EXACTA — shrink obligatorio al arreglar) ──────
// Formato P-22: "<plugin> importa @/entities/<entity> (owner: <owner>)"
// F4c eliminó el último ofensor (chats→order ahora va por el cast order-ref).
const EXPECTED_P22_OFFENDERS: string[] = [];

// Formato P-23: "<owner-del-archivo> → <literal>"
// F5 eliminó las últimas (evals server-side bajo /api/agents). CERO deuda:
// todo literal /api del código pertenece al owner de su archivo.
const EXPECTED_P23_OFFENDERS: string[] = [];

// ── Helpers ────────────────────────────────────────────────────────────────

function walk(dir: string, exts: string[]): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (entry === "node_modules" || entry.startsWith(".")) continue;
      out.push(...walk(full, exts));
    } else if (exts.some((e) => entry.endsWith(e))) {
      out.push(full);
    }
  }
  return out;
}

/** Quita comentarios (// y bloques) y strings de template-import irrelevantes. */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "")
    .replace(/([^:])\/\/[^\n]*/g, "$1");
}

function owners(): Map<string, string> {
  // OWNERS.yaml fue el mapa TRANSITORIO de F3 mientras existían entities
  // centrales. Post-F4/F5 (todas migradas) ya no existe: toda entity vive
  // bajo plugins/<owner>/frontend/entities/ y el owner es el plugin del path.
  const path = join(ENTITIES_DIR, "OWNERS.yaml");
  if (!existsSync(path)) return new Map();
  const doc = parse(readFileSync(path, "utf-8")) as Record<string, string>;
  return new Map(Object.entries(doc ?? {}));
}

function pluginIds(): string[] {
  return readdirSync(PLUGINS_DIR).filter(
    (d) =>
      !d.startsWith("_") &&
      !d.startsWith(".") &&
      existsSync(join(PLUGINS_DIR, d, "plugin.yaml")),
  );
}

/** prefix → plugin owner, desde los manifests (api.prefix + legacy_routers). */
function apiPrefixOwners(): Map<string, string> {
  const out = new Map<string, string>();
  for (const id of pluginIds()) {
    const manifest = parse(
      readFileSync(join(PLUGINS_DIR, id, "plugin.yaml"), "utf-8"),
    ) as {
      api?: { prefix?: string; legacy_routers?: { prefix?: string }[] };
    };
    if (!manifest?.api) continue;
    if (manifest.api.prefix) out.set(manifest.api.prefix, id);
    for (const r of manifest.api.legacy_routers ?? []) {
      if (r.prefix) out.set(r.prefix, id);
    }
  }
  return out;
}

/** Longest-prefix match de un path /api/... contra los prefixes declarados. */
function resolveApiOwner(path: string, prefixes: Map<string, string>): string | null {
  let best: string | null = null;
  let bestLen = -1;
  for (const [prefix, owner] of prefixes) {
    if (
      (path === prefix || path.startsWith(prefix + "/") || path.startsWith(prefix + "?")) &&
      prefix.length > bestLen
    ) {
      best = owner;
      bestLen = prefix.length;
    }
  }
  return best;
}

const API_LITERAL_RE = /["'`](\/api\/[A-Za-z0-9_\-./${}?=&]*)/g;
const ENTITY_IMPORT_RE = /from\s+["']@\/entities\/([a-z0-9-]+)/g;

// ── P-11 — src/entities/ central queda VACÍO ───────────────────────────────

describe("P-11 — toda entity de dominio es plugin-local (INV-1)", () => {
  test("src/entities/ no contiene ninguna entity (sin allowlist)", () => {
    const dirs = existsSync(ENTITIES_DIR)
      ? readdirSync(ENTITIES_DIR).filter((d) =>
          statSync(join(ENTITIES_DIR, d)).isDirectory(),
        )
      : [];
    // Sin shared entities: el caso cross-plugin va por cast declarado (P-14).
    // Agregar una entity acá = retroceder a F2 de la auditoría original.
    expect(dirs).toEqual([]);
  });

  test("ningún archivo del repo importa @/entities/* (alias muerto)", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC, [".ts", ".tsx"])) {
      if (file.includes("/test/architecture/")) continue;
      const code = stripComments(readFileSync(file, "utf-8"));
      if (/from\s+["']@\/entities\//.test(code)) {
        offenders.push(file.slice(SRC.length + 1));
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ── P-22 — ownership de imports de entities ───────────────────────────────

describe("P-22 — un plugin no importa entities de otro owner (canal lavado)", () => {
  test("imports @/entities/* desde plugins/ respetan OWNERS.yaml", () => {
    const ownerMap = owners();
    const offenders = new Set<string>();
    const unknown: string[] = [];

    for (const pid of pluginIds()) {
      const files = walk(join(PLUGINS_DIR, pid), [".ts", ".tsx"]);
      for (const file of files) {
        const code = stripComments(readFileSync(file, "utf-8"));
        for (const m of code.matchAll(ENTITY_IMPORT_RE)) {
          const entity = m[1];
          const owner = ownerMap.get(entity);
          if (!owner) {
            unknown.push(`${pid}: @/entities/${entity} sin entry en OWNERS.yaml`);
            continue;
          }
          if (owner !== pid) {
            offenders.add(`${pid} importa @/entities/${entity} (owner: ${owner})`);
          }
        }
      }
    }

    expect(unknown, "Toda entity central debe tener owner declarado").toEqual([]);
    expect([...offenders].sort()).toEqual(EXPECTED_P22_OFFENDERS);
  });
});

// ── P-23 — ownership de literales /api/ en código ─────────────────────────

describe("P-23 — los /api/<x> del CÓDIGO pertenecen al owner del archivo", () => {
  test("literales /api en entities/ y plugins/ resuelven a su owner", () => {
    const ownerMap = owners();
    const prefixes = apiPrefixOwners();
    const offenders = new Set<string>();
    const unresolved: string[] = [];

    // (owner, archivo[]) — entities centrales por OWNERS + cada plugin.
    const scopes: Array<[string, string[]]> = [];
    for (const [entity, owner] of ownerMap) {
      scopes.push([owner, walk(join(ENTITIES_DIR, entity), [".ts", ".tsx"])]);
    }
    for (const pid of pluginIds()) {
      scopes.push([pid, walk(join(PLUGINS_DIR, pid), [".ts", ".tsx"])]);
    }

    for (const [fileOwner, files] of scopes) {
      for (const file of files) {
        if (file.endsWith(".test.ts") || file.endsWith(".test.tsx")) continue;
        const code = stripComments(readFileSync(file, "utf-8"));
        for (const m of code.matchAll(API_LITERAL_RE)) {
          // Normalizar: cortar query/template para el match de prefijo.
          const literal = m[1].split("${")[0].split("?")[0].replace(/\/$/, "");
          if (literal === "/api") continue; // base genérica del client
          const apiOwner = resolveApiOwner(literal, prefixes);
          if (!apiOwner) {
            unresolved.push(`${fileOwner}: ${literal} no matchea ningún prefix de manifest`);
            continue;
          }
          if (apiOwner !== fileOwner) {
            offenders.add(`${fileOwner} → ${literal}`);
          }
        }
      }
    }

    expect(
      unresolved,
      "Todo /api/<x> del código debe pertenecer a un prefix declarado en un manifest",
    ).toEqual([]);
    expect([...offenders].sort()).toEqual(EXPECTED_P23_OFFENDERS);
  });
});
