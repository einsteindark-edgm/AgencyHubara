/**
 * Env vars expuestas al frontend. Solo `VITE_*` viajan al bundle (Vite/Tauri).
 *
 * Si falta una requerida, fallamos al import-time para que el dev se entere
 * antes de hacer un fetch que devuelve undefined.
 */

function required(name: string, value: string | undefined): string {
  if (!value || value.trim() === "") {
    throw new Error(
      `Missing required env var: ${name}. Define it in .env.development or .env.local.`,
    );
  }
  return value;
}

export const env = {
  apiUrl: required("VITE_API_URL", import.meta.env.VITE_API_URL),
  // OTel web (Tier 2): endpoint OTLP-HTTP del collector al que el browser manda
  // traces. Opcional — default al collector local (puerto 4318 del compose). En prod
  // apuntar al collector público vía VITE_OTEL_EXPORTER_URL.
  otelExporterUrl:
    import.meta.env.VITE_OTEL_EXPORTER_URL ?? "http://localhost:4318/v1/traces",
} as const;
