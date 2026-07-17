/**
 * Modelo de dominio de la audiencia por segmentos (camelCase).
 */

export interface AudienceSegment {
  key: string;
  label: string;
  description: string;
  count: number;
}

export interface SegmentsInfo {
  segments: AudienceSegment[];
  /** Contactos que JAMÁS reciben campañas: atendidos por humano u opt-out. */
  excludedCount: number;
  /** Tarifa marketing por mensaje del rate card vigente (USD micros). */
  unitCostUsdMicros: number;
  currency: string;
}

/** Labels de fallback para pintar chips de segmento SIN fetch (las cards del
 *  sidebar no deben pegarle a /segments) — espejo de `_SEGMENT_LABELS`. */
const FALLBACK_LABELS: Record<string, string> = {
  clientes: "Clientes",
  interesados: "Interesados",
  frios: "Fríos",
};

export function segmentLabel(key: string): string {
  return (
    FALLBACK_LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1)
  );
}

/** Destinatarios totales de los segmentos elegidos. */
export function recipientsForSelection(
  info: SegmentsInfo | undefined,
  selected: string[],
): number {
  if (!info) return 0;
  return info.segments
    .filter((s) => selected.includes(s.key))
    .reduce((acc, s) => acc + s.count, 0);
}
