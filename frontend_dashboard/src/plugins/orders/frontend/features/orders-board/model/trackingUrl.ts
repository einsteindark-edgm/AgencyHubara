/**
 * Normaliza el link de la guía que pega el operador en el modal "En camino".
 *
 *  - trim; vacío → null (= "marcar en camino sin guía");
 *  - `http(s)://…` se respeta tal cual;
 *  - un dominio pelado (`coordinadora.com/rastreo?guia=1`) recibe `https://`
 *    — WhatsApp solo linkifica con esquema y el backend exige http(s);
 *  - cualquier otro esquema (`javascript:`, `ftp:`), espacios internos o algo
 *    que no parsea como URL → null (el modal muestra el error y no confirma).
 *
 * Espejo del validador del backend (`_parse_tracking_url` en la orders API).
 */
const MAX_LEN = 500;

export function normalizeTrackingUrl(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed || /\s/.test(trimmed)) return null;

  // Esquema explícito distinto de http(s) → inválido (no se autocorrige).
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(trimmed)?.[1]?.toLowerCase();
  if (scheme && scheme !== "http" && scheme !== "https") return null;

  const candidate = scheme ? trimmed : `https://${trimmed}`;
  if (candidate.length > MAX_LEN) return null;
  try {
    const url = new URL(candidate);
    // Un host sin punto ("solo", "localhost") no es un link de rastreo útil.
    if (!url.hostname.includes(".")) return null;
  } catch {
    return null;
  }
  return candidate;
}
