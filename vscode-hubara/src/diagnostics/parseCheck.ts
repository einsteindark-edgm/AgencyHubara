/**
 * Parsers PUROS del output de `check` — dos formatos DISTINTOS, uno por
 * lado (verificado contra el código fuente real, no adivinado):
 *
 * GraphAgents (`sdk/cli.py:cmd_check`) — una línea por manifest, filename
 * crudo (no path), sin bloque rustc:
 *   error[C0-SCHEMA] greeter.agent.yaml: <mensaje de parseo roto>
 *   error <manifest>: <mensaje>
 *   warn  <manifest>: <mensaje>
 *   ok    <manifest>  (archetype, nivel C2)
 *
 * Hubara backend (`src/sdk/diagnostics.py:format_diagnostic` +
 * `src/sdk/cli/__init__.py:_render_report`) — bloque estilo rustc con
 * location/fix/ref para errores, línea simple para warnings:
 *   error[P-27]: <título>
 *     --> <location>
 *     <detail>
 *     fix: <...>
 *     ref: <...>
 *
 *   warning[P-29]: <detail>
 */

export interface CheckDiagnostic {
  severity: "error" | "warning";
  code?: string;
  message: string;
  /** GraphAgents: basename del manifest. Acktos: path absoluto O relativo al
   * monorepo root (mezclado según qué check lo emitió) — se resuelve en el
   * caller, que conoce las raíces de cada lado. */
  location?: string;
}

export function parseGraphAgentsCheckOutput(text: string): CheckDiagnostic[] {
  const diags: CheckDiagnostic[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const errorMatch = /^error(?:\[([\w-]+)\])?\s+(\S+):\s*(.*)$/.exec(raw);
    if (errorMatch) {
      diags.push({ severity: "error", code: errorMatch[1], location: errorMatch[2], message: errorMatch[3] });
      continue;
    }
    const warnMatch = /^warn\s+(\S+):\s*(.*)$/.exec(raw);
    if (warnMatch) {
      diags.push({ severity: "warning", location: warnMatch[1], message: warnMatch[2] });
    }
  }
  return diags;
}

interface Accumulator {
  severity: "error" | "warning";
  code?: string;
  headerText: string;
  location?: string;
  detailLines: string[];
}

export function parseHubaraCheckOutput(text: string): CheckDiagnostic[] {
  const diags: CheckDiagnostic[] = [];
  let current: Accumulator | null = null;

  const flush = () => {
    if (current) {
      const message =
        current.detailLines.length > 0 ? `${current.headerText}: ${current.detailLines.join(" ")}` : current.headerText;
      diags.push({ severity: current.severity, code: current.code, message, location: current.location });
    }
    current = null;
  };

  for (const raw of text.split(/\r?\n/)) {
    const header = /^(error|warning)\[([\w-]+)\]:\s*(.*)$/.exec(raw);
    if (header) {
      flush();
      current = { severity: header[1] as "error" | "warning", code: header[2], headerText: header[3], detailLines: [] };
      continue;
    }
    if (!current) {
      continue;
    }
    if (raw.trim() === "") {
      flush();
      continue;
    }
    const loc = /^\s{2}-->\s*(.+)$/.exec(raw);
    if (loc) {
      current.location = loc[1].trim();
      continue;
    }
    if (/^\s{2}fix:/.test(raw) || /^\s{2}ref:/.test(raw)) {
      continue; // se documentan en `explain <code>`, no hace falta duplicarlos en el squiggle
    }
    const detail = /^\s{2}(.+)$/.exec(raw);
    if (detail) {
      current.detailLines.push(detail[1].trim());
    }
  }
  flush();
  return diags;
}
