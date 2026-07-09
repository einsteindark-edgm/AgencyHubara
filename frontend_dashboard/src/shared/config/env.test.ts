/**
 * Regresión del incidente 2026-07-08: el build de PROD salía con el tracing
 * del browser encendido apuntando al default `http://localhost:4318/v1/traces`
 * (el deploy no setea `VITE_OTEL_EXPORTER_URL`). Un dashboard servido desde
 * CloudFront posteando a localhost = bloqueado por Chrome (Local Network
 * Access) → warning permanente en la consola del operador, cero traces.
 *
 * Contrato: en prod el tracing solo se enciende si hay un exporter EXPLÍCITO
 * (o si `VITE_OTEL_ENABLED=1` lo fuerza). El default localhost queda para dev.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

async function loadEnv(stub: Record<string, unknown>): Promise<typeof import("./env")> {
  vi.resetModules();
  for (const [k, v] of Object.entries(stub)) {
    vi.stubEnv(k, v as string);
  }
  return import("./env");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("env.otelEnabled — prod sin exporter explícito", () => {
  it("queda APAGADO en prod si no hay VITE_OTEL_EXPORTER_URL (default localhost no sirve tras CloudFront)", async () => {
    const { env } = await loadEnv({
      VITE_API_URL: "https://api.example.com",
      PROD: true,
      VITE_OTEL_ENABLED: undefined,
      VITE_OTEL_EXPORTER_URL: undefined,
    });
    expect(env.otelEnabled).toBe(false);
  });

  it("se enciende en prod cuando el deploy provee el exporter", async () => {
    const { env } = await loadEnv({
      VITE_API_URL: "https://api.example.com",
      PROD: true,
      VITE_OTEL_ENABLED: undefined,
      VITE_OTEL_EXPORTER_URL: "https://otel.example.com/v1/traces",
    });
    expect(env.otelEnabled).toBe(true);
  });

  it("VITE_OTEL_ENABLED=1 sigue forzando ON aun sin exporter (opt-in dev)", async () => {
    const { env } = await loadEnv({
      VITE_API_URL: "https://api.example.com",
      PROD: false,
      VITE_OTEL_ENABLED: "1",
      VITE_OTEL_EXPORTER_URL: undefined,
    });
    expect(env.otelEnabled).toBe(true);
  });
});
