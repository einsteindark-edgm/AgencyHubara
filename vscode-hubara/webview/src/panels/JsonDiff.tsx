import React from "react";

/**
 * Diff estructural de dos valores JSON — la vista "confrontada" del
 * entró/salió de un nodo (§F9). Como el acc se ACUMULA a través del pod, el
 * output de un nodo suele ser su input + lo que agregó/cambió: el diff hace
 * visible exactamente ESO. Lógica pura (sin DOM) — testeable standalone.
 */
export type DiffKind = "same" | "add" | "del" | "ctx";

export interface DiffLine {
  kind: DiffKind;
  indent: number;
  text: string;
}

const MAX_LINES = 500;
const MAX_VALUE_CHARS = 160;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (isObj(a) && isObj(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => deepEqual(a[k], b[k]));
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
  }
  return false;
}

function compact(v: unknown): string {
  const s = v === undefined ? "undefined" : JSON.stringify(v);
  return s.length > MAX_VALUE_CHARS ? `${s.slice(0, MAX_VALUE_CHARS)}…` : s;
}

/** 0..1 — proporción de claves top-level con valor idéntico. 0 = "completamente
 * diferente" (ahí el diff no aporta: se muestran los dos planos, sin colores). */
export function similarity(before: unknown, after: unknown): number {
  if (deepEqual(before, after)) {
    return 1;
  }
  if (isObj(before) && isObj(after)) {
    const union = new Set([...Object.keys(before), ...Object.keys(after)]);
    if (union.size === 0) {
      return 1;
    }
    let shared = 0;
    for (const k of union) {
      if (k in before && k in after && deepEqual(before[k], after[k])) {
        shared++;
      }
    }
    return shared / union.size;
  }
  return 0;
}

export function diffJson(before: unknown, after: unknown, indent = 0, label = ""): DiffLine[] {
  const prefix = label ? `${label}: ` : "";
  if (deepEqual(before, after)) {
    return [{ kind: "same", indent, text: `${prefix}${compact(before)}` }];
  }
  if (before === undefined) {
    return [{ kind: "add", indent, text: `${prefix}${compact(after)}` }];
  }
  if (after === undefined) {
    return [{ kind: "del", indent, text: `${prefix}${compact(before)}` }];
  }
  if (isObj(before) && isObj(after)) {
    const lines: DiffLine[] = [{ kind: "ctx", indent, text: `${prefix}{` }];
    // orden estable: primero las claves del ANTES, después las nuevas del DESPUÉS
    const keys = [...Object.keys(before), ...Object.keys(after).filter((k) => !(k in before))];
    for (const k of keys) {
      lines.push(...diffJson(before[k], after[k], indent + 1, k));
    }
    lines.push({ kind: "ctx", indent, text: "}" });
    return lines;
  }
  if (Array.isArray(before) && Array.isArray(after)) {
    const lines: DiffLine[] = [{ kind: "ctx", indent, text: `${prefix}[` }];
    const max = Math.max(before.length, after.length);
    for (let i = 0; i < max; i++) {
      lines.push(
        ...diffJson(i < before.length ? before[i] : undefined, i < after.length ? after[i] : undefined, indent + 1, `[${i}]`),
      );
    }
    lines.push({ kind: "ctx", indent, text: "]" });
    return lines;
  }
  // escalares (o tipos distintos): el clásico −viejo / +nuevo
  return [
    { kind: "del", indent, text: `${prefix}${compact(before)}` },
    { kind: "add", indent, text: `${prefix}${compact(after)}` },
  ];
}

export interface JsonDiffProps {
  before: unknown;
  after: unknown;
}

export function JsonDiff({ before, after }: JsonDiffProps): React.ReactElement {
  // "completamente diferente" → el diff no aporta: los dos valores planos.
  if (similarity(before, after) === 0) {
    return (
      <div>
        <div className="subtool-sec">entró</div>
        <pre className="io-pre">{pretty(before)}</pre>
        <div className="subtool-sec">salió (sin relación estructural con el entró — sin diff)</div>
        <pre className="io-pre">{pretty(after)}</pre>
      </div>
    );
  }
  const lines = diffJson(before, after);
  const shown = lines.slice(0, MAX_LINES);
  return (
    <div>
      <div className="diff-legend">
        <span className="diff-line diff-del">− solo en el entró</span>
        <span className="diff-line diff-add">+ agregado/cambiado al salir</span>
      </div>
      <pre className="io-pre diff-pre">
        {shown.map((l, i) => (
          <div key={i} className={`diff-line diff-${l.kind}`}>
            {l.kind === "add" ? "+ " : l.kind === "del" ? "− " : "  "}
            {"  ".repeat(l.indent)}
            {l.text}
          </div>
        ))}
        {lines.length > MAX_LINES && <div className="diff-line diff-ctx">… ({lines.length - MAX_LINES} líneas más)</div>}
      </pre>
    </div>
  );
}

function pretty(v: unknown): string {
  if (v == null) {
    return "";
  }
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}
