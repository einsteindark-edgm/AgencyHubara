/**
 * Tests de la persistencia de sesión de la app móvil. El refresh token vive en
 * localStorage (sandbox de la app en Android) para no re-loguear en cada
 * apertura; el access token se rehidrata desde ahí al arrancar.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  saveSession,
  loadSession,
  clearSession,
  computeExpiresAt,
  type PersistedSession,
} from "./session-store";

const sample: PersistedSession = {
  accessToken: "acc",
  idToken: "id",
  refreshToken: "ref",
  expiresAt: 9_999_999_999_000,
  username: "op@hubara.co",
};

beforeEach(() => {
  localStorage.clear();
});

describe("session-store", () => {
  it("save → load round-trip", () => {
    saveSession(sample);
    expect(loadSession()).toEqual(sample);
  });

  it("load devuelve null sin sesión", () => {
    expect(loadSession()).toBeNull();
  });

  it("clear borra la sesión", () => {
    saveSession(sample);
    clearSession();
    expect(loadSession()).toBeNull();
  });

  it("load devuelve null y limpia si el JSON está corrupto", () => {
    localStorage.setItem("hubara.session.v1", "{no-json");
    expect(loadSession()).toBeNull();
  });

  it("load devuelve null si falta el refresh token (sesión inservible)", () => {
    localStorage.setItem(
      "hubara.session.v1",
      JSON.stringify({ ...sample, refreshToken: "" }),
    );
    expect(loadSession()).toBeNull();
  });

  it("computeExpiresAt convierte expiresIn (s) a epoch ms con margen", () => {
    const now = 1_000_000_000_000;
    // 3600s → +3600_000ms, menos 60s de colchón de seguridad.
    expect(computeExpiresAt(3600, now)).toBe(now + 3600_000 - 60_000);
  });
});
