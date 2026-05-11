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
} as const;
