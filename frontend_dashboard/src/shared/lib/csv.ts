/**
 * CSV para Excel en español (reporte de ventas de Órdenes, 2026-11-06).
 *
 * Excel en español usa `;` como separador (la coma es el decimal) y solo
 * reconoce UTF-8 con BOM: sin él, las tildes y la «ñ» salen rotas. Filas
 * separadas con CRLF; un campo con separador, comillas o salto de línea va
 * entre comillas, con las comillas dobladas. Un texto que empieza como
 * fórmula (=, +, -, @, tabulador, retorno) lleva un apóstrofo delante.
 */

export type CsvCell = string | number;

const BOM = String.fromCharCode(0xfeff);

/** Lo que Excel lee como fórmula al empezar una celda. */
const FORMULA_START = /^[=+\-@\t\r]/;

function cell(value: CsvCell, sep: string): string {
  // Un texto que empieza como fórmula se neutraliza con un apóstrofo (un
  // nombre de cliente lo escribe un tercero); los números quedan como números.
  const text = typeof value === "string" && FORMULA_START.test(value) ? `'${value}` : String(value);
  return text.includes(sep) || /["\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: CsvCell[][], sep = ";"): string {
  return BOM + rows.map((row) => row.map((v) => cell(v, sep)).join(sep)).join("\r\n") + "\r\n";
}

/** Descarga un texto como archivo, con un enlace temporal. No hace nada donde
 *  no hay `URL.createObjectURL` (jsdom en las pruebas). */
export function downloadText(filename: string, text: string, mime = "text/csv;charset=utf-8"): void {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
