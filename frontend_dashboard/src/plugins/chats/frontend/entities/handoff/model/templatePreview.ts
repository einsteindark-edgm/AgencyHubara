/**
 * Previsualización de una plantilla de WhatsApp tal como la recibe el cliente.
 *
 * Puro: parte el `body` aprobado en segmentos de texto fijo y slots `{{N}}`.
 * Un slot vacío se muestra como `{ tu texto aquí }` (o `{ <descripción> }`
 * cuando la plantilla tiene varios), y se llena con lo que escribe el operador.
 */

import type { WhatsAppTemplate } from "../contracts";

export type TemplatePreviewSegment =
  | { kind: "text"; text: string }
  | { kind: "slot"; name: string; text: string; filled: boolean };

const SLOT = /\{\{(\d+)\}\}/g;

export function buildTemplatePreview(
  template: WhatsAppTemplate,
  values: Record<string, string>,
): TemplatePreviewSegment[] {
  const body = template.body ?? "";
  const single = template.variables.length === 1;
  const segments: TemplatePreviewSegment[] = [];
  let cursor = 0;

  for (const match of body.matchAll(SLOT)) {
    const start = match.index ?? 0;
    if (start > cursor) segments.push({ kind: "text", text: body.slice(cursor, start) });
    cursor = start + match[0].length;

    const variable = template.variables[Number(match[1]) - 1];
    if (!variable) {
      segments.push({ kind: "text", text: match[0] });
      continue;
    }
    const value = values[variable.name] ?? "";
    const filled = value.trim().length > 0;
    const placeholder = single
      ? "{ tu texto aquí }"
      : `{ ${variable.description ?? variable.name} }`;
    segments.push({
      kind: "slot",
      name: variable.name,
      text: filled ? value : placeholder,
      filled,
    });
  }
  if (cursor < body.length) segments.push({ kind: "text", text: body.slice(cursor) });
  return segments;
}

/** Todas las variables con texto no vacío → se puede enviar. */
export function isTemplateReady(
  template: WhatsAppTemplate,
  values: Record<string, string>,
): boolean {
  return template.variables.every((v) => (values[v.name] ?? "").trim().length > 0);
}

/** Meta rechaza params con saltos de línea, tabs o >4 espacios seguidos:
 *  los normalizamos mientras el operador escribe. */
export function sanitizeTemplateParam(value: string): string {
  return value.replace(/[\r\n\t]+/g, " ").replace(/ {2,}/g, " ");
}
