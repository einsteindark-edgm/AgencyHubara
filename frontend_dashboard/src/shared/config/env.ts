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
  // `true` SOLO en el build dedicado de la app móvil (Android/iOS) — lo prende
  // el script `tauri:android:*` con `VITE_MOBILE_APP=1`. Decide renderizar el
  // shell de chats (una columna + login nativo) en vez del Dashboard. NO se
  // deriva del ancho de pantalla: un desktop angosto NO es la app móvil, y la
  // app móvil en un viewport ancho SIGUE siendo la app de chats.
  mobileApp: import.meta.env.VITE_MOBILE_APP === "1",
  // OTel web (Tier 2): endpoint OTLP-HTTP del collector al que el browser manda
  // traces. Opcional — default al collector local (puerto 4318 del compose). En prod
  // apuntar al collector público vía VITE_OTEL_EXPORTER_URL.
  otelExporterUrl:
    import.meta.env.VITE_OTEL_EXPORTER_URL ?? "http://localhost:4318/v1/traces",
  // F6.6 (auditoría 2026-06-10): el tracing del browser es OPT-IN en dev
  // (sin collector corriendo, los POST a /v1/traces solo ensucian la Network
  // tab — parte del síntoma "traces llamándose cada rato"). En prod solo se
  // enciende si el deploy provee un exporter EXPLÍCITO: el default localhost
  // detrás de CloudFront es un POST que Chrome bloquea (Local Network Access)
  // — warning permanente en la consola del operador y cero traces (incidente
  // 2026-07-08). `VITE_OTEL_ENABLED=1|0` fuerza en cualquier modo.
  otelEnabled:
    import.meta.env.VITE_OTEL_ENABLED !== undefined
      ? import.meta.env.VITE_OTEL_ENABLED === "1"
      : Boolean(import.meta.env.PROD && import.meta.env.VITE_OTEL_EXPORTER_URL),
  // Muestreo de traces en prod (0..1). 1 = todo (default actual); bajarlo
  // reduce el volumen de POSTs sin tocar código.
  otelSampleRate: Number(import.meta.env.VITE_OTEL_SAMPLE_RATE ?? "1"),
  // Cognito (OIDC) — login del dashboard (Authorization Code + PKCE). `authority`
  // = issuer del user pool. OPCIONALES: si faltan (dev local), `cognitoEnabled`
  // queda false → la app no exige login (espeja al backend, que es no-op sin
  // COGNITO_*: enforce-only-when-configured). En prod el build los inyecta.
  cognitoAuthority: import.meta.env.VITE_COGNITO_AUTHORITY ?? "",
  cognitoClientId: import.meta.env.VITE_COGNITO_CLIENT_ID ?? "",
  cognitoRedirectUri:
    import.meta.env.VITE_COGNITO_REDIRECT_URI ??
    `${window.location.origin}/callback`,
  // Región AWS del user pool — la app móvil pega directo al endpoint
  // `cognito-idp.<region>.amazonaws.com` (flujo nativo USER_PASSWORD_AUTH, sin
  // navegador). Se deriva del authority (`https://cognito-idp.<region>.amazonaws.com/<poolId>`)
  // o se fuerza con VITE_COGNITO_REGION.
  cognitoRegion:
    import.meta.env.VITE_COGNITO_REGION ??
    deriveCognitoRegion(import.meta.env.VITE_COGNITO_AUTHORITY ?? ""),
  // Endpoint del IDP de Cognito para el login nativo móvil. Se arma acá (capa
  // de config/URLs) para que el cliente `shared/api/cognito.ts` no lleve la URL
  // hardcodeada. `""` si no hay región configurada.
  cognitoIdpEndpoint: buildCognitoIdpEndpoint(
    import.meta.env.VITE_COGNITO_REGION ??
      deriveCognitoRegion(import.meta.env.VITE_COGNITO_AUTHORITY ?? ""),
  ),
  cognitoEnabled: Boolean(
    import.meta.env.VITE_COGNITO_AUTHORITY &&
      import.meta.env.VITE_COGNITO_CLIENT_ID,
  ),
} as const;

/** Extrae la región del authority de Cognito. `""` si no matchea. */
function deriveCognitoRegion(authority: string): string {
  const m = authority.match(/cognito-idp\.([a-z0-9-]+)\.amazonaws\.com/i);
  return m ? m[1] : "";
}

/** URL del endpoint IDP de Cognito para una región. `""` si no hay región. */
function buildCognitoIdpEndpoint(region: string): string {
  return region ? `https://cognito-idp.${region}.amazonaws.com/` : "";
}
