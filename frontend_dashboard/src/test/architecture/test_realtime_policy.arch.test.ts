/**
 * Test #15 — política de realtime (F1/F6, auditoría 2026-06-10).
 *
 * El dashboard es push-first: UNA conexión SSE multiplexada
 * (`shared/api/events`) empuja eventos por dominio y los plugins invalidan
 * sus queries. El polling quedó relegado a:
 *   (a) fallback lento — `refetchInterval` numérico ≥ 60_000 ms, o
 *   (b) function-form acotado a un run activo (patrón catalog: poll rápido
 *       SOLO mientras `status === "running"`), en archivos allowlisteados.
 *
 * Sin este gate, el anti-patrón original regresa silencioso: orders pollaba
 * cada 30s, chats el detalle cada 3s, catalog cada 5s — el síntoma "la
 * pantalla llama orders y traces cada rato" que disparó la auditoría.
 *
 * También protege la propiedad estructural que evita fugas cross-sección:
 * el shell monta UNA sola Page (lazy, vía registry); nadie importa Pages de
 * plugins eager, y nadie abre EventSource/subscribeSse fuera de shared/api.
 */
import { describe, expect, it } from "vitest";

import { iterSrcFiles, readUtf8, relToFrontend } from "./helpers";

/** Mínimo permitido para un `refetchInterval` numérico (fallback lento). */
const MIN_FALLBACK_MS = 60_000;

/** Archivos con permiso de function-form (poll rápido acotado a run activo). */
const FUNCTION_FORM_ALLOWLIST = new Set([
  "src/plugins/catalog/frontend/entities/catalog-sync/api.ts",
]);

const NUMERIC_INTERVAL_REGEX = /refetchInterval:\s*([\d][\d_]*)/g;
const FUNCTION_INTERVAL_REGEX = /refetchInterval:\s*\(/;

describe("R-REALTIME — push-first, polling solo como fallback", () => {
  it("ningún refetchInterval numérico < 60s; function-form solo allowlisteado", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
      if (rel.startsWith("src/test/")) continue;

      const text = readUtf8(abs);

      for (const match of text.matchAll(NUMERIC_INTERVAL_REGEX)) {
        const ms = Number(match[1].replaceAll("_", ""));
        if (ms < MIN_FALLBACK_MS) {
          offenders.push(`${rel}: refetchInterval ${ms}ms (< ${MIN_FALLBACK_MS})`);
        }
      }

      if (
        FUNCTION_INTERVAL_REGEX.test(text) &&
        !FUNCTION_FORM_ALLOWLIST.has(rel)
      ) {
        offenders.push(`${rel}: refetchInterval function-form fuera del allowlist`);
      }
    }

    expect(
      offenders,
      `R-REALTIME violations — el polling primario está prohibido (F1):\n` +
        offenders.map((p) => `  - ${p}`).join("\n") +
        `\n\nUsá el event stream (useDashboardEvents en tu entity, evento ` +
        `publicado por el backend en src/platform/events) + refetchInterval ` +
        `≥ 60_000 como fallback. Function-form SOLO para observar un run ` +
        `activo, agregando el archivo al allowlist de este gate (con ADR).`,
    ).toEqual([]);
  });

  it("solo shared/api abre conexiones SSE (EventSource/subscribeSse)", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      if (rel.startsWith("src/shared/api/")) continue;
      if (rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;
      if (rel.startsWith("src/test/")) continue;

      const text = readUtf8(abs);
      if (/new EventSource\(|subscribeSse\(/.test(text)) {
        offenders.push(rel);
      }
    }

    expect(
      offenders,
      `Conexiones SSE fuera de shared/api (rompe la política de UNA conexión):\n` +
        offenders.map((p) => `  - ${p}`).join("\n") +
        `\n\nSuscribite con useDashboardEvents("<dominio>", handler) — el ` +
        `EventStreamProvider (app/providers) ya mantiene la única conexión.`,
    ).toEqual([]);
  });

  it("el shell no importa Pages de plugins (single-page mount vía registry lazy)", () => {
    const offenders: string[] = [];

    for (const abs of iterSrcFiles("pages/**/*.{ts,tsx}")) {
      const rel = relToFrontend(abs);
      const text = readUtf8(abs);
      if (/from\s+["']@plugins\//.test(text)) {
        offenders.push(rel);
      }
    }

    expect(
      offenders,
      `El shell (src/pages) importa de @plugins (acople shell→dominio):\n` +
        offenders.map((p) => `  - ${p}`).join("\n") +
        `\n\nLas Pages llegan SOLO por el registry generado (lazy). Importar ` +
        `un plugin eager desde el shell rompe el code-splitting y la garantía ` +
        `de que una sección desmontada no pollea (auditoría §1.2).`,
    ).toEqual([]);
  });
});
