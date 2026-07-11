/**
 * Tests de la máquina de auth móvil: la decisión de boot (pura) + los flujos
 * del hook (signIn éxito/error/contraseña-nueva, signOut) con Cognito mockeado.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import type { PersistedSession } from "@/shared/config";
import { decideBoot, useMobileAuth } from "./useMobileAuth";

// ── Mocks del cliente Cognito y de la persistencia ─────────────────────────
const initiateAuth = vi.fn();
const refresh = vi.fn();
const respondNewPassword = vi.fn();

vi.mock("@/shared/api", () => ({
  cognitoInitiateAuth: (...a: unknown[]) => initiateAuth(...a),
  cognitoRefresh: (...a: unknown[]) => refresh(...a),
  cognitoRespondNewPassword: (...a: unknown[]) => respondNewPassword(...a),
}));

const setAccessTokenMock = vi.fn();
const saveSessionMock = vi.fn();
const clearSessionMock = vi.fn();
let loaded: PersistedSession | null = null;

vi.mock("@/shared/config", () => ({
  env: {
    cognitoRegion: "us-east-1",
    cognitoIdpEndpoint: "https://cognito-idp.us-east-1.amazonaws.com/",
    cognitoClientId: "cid",
  },
  setAccessToken: (t: string | null) => setAccessTokenMock(t),
  saveSession: (s: PersistedSession) => saveSessionMock(s),
  clearSession: () => clearSessionMock(),
  loadSession: () => loaded,
  computeExpiresAt: (expiresIn: number, now: number) => now + expiresIn * 1000,
}));

beforeEach(() => {
  initiateAuth.mockReset();
  refresh.mockReset();
  respondNewPassword.mockReset();
  setAccessTokenMock.mockReset();
  saveSessionMock.mockReset();
  clearSessionMock.mockReset();
  loaded = null;
});

describe("decideBoot", () => {
  const s: PersistedSession = {
    accessToken: "a",
    idToken: "i",
    refreshToken: "r",
    expiresAt: 1000,
    username: "op",
  };

  it("sin sesión → unauthenticated", () => {
    expect(decideBoot(null, 500).action).toBe("unauthenticated");
  });
  it("access token vigente → authenticated", () => {
    expect(decideBoot(s, 500).action).toBe("authenticated");
  });
  it("access token vencido → refresh", () => {
    expect(decideBoot(s, 2000).action).toBe("refresh");
  });
});

describe("useMobileAuth", () => {
  it("arranca en unauthenticated cuando no hay sesión persistida", async () => {
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );
  });

  it("signIn exitoso → authenticated + persiste + setea el token en memoria", async () => {
    initiateAuth.mockResolvedValue({
      kind: "tokens",
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresIn: 3600,
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));

    await act(async () => {
      await result.current.signIn("op@hubara.co", "clave");
    });

    expect(result.current.state).toMatchObject({
      status: "authenticated",
      username: "op@hubara.co",
    });
    expect(setAccessTokenMock).toHaveBeenCalledWith("ACC");
    expect(saveSessionMock).toHaveBeenCalledWith(
      expect.objectContaining({ refreshToken: "REF", username: "op@hubara.co" }),
    );
  });

  it("signIn con credenciales malas → unauthenticated con error", async () => {
    initiateAuth.mockResolvedValue({
      kind: "error",
      code: "NotAuthorizedException",
      message: "Email o contraseña incorrectos.",
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));

    await act(async () => {
      await result.current.signIn("op@hubara.co", "mala");
    });

    expect(result.current.state).toMatchObject({
      status: "unauthenticated",
      error: "Email o contraseña incorrectos.",
    });
    expect(setAccessTokenMock).not.toHaveBeenCalledWith(expect.any(String));
  });

  it("signIn con usuario nuevo → new_password_required, y completeNewPassword autentica", async () => {
    initiateAuth.mockResolvedValue({
      kind: "new_password_required",
      session: "SESS",
      username: "op@hubara.co",
    });
    respondNewPassword.mockResolvedValue({
      kind: "tokens",
      accessToken: "ACC2",
      idToken: "ID2",
      refreshToken: "REF2",
      expiresIn: 3600,
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));

    await act(async () => {
      await result.current.signIn("op@hubara.co", "temp");
    });
    expect(result.current.state.status).toBe("new_password_required");

    await act(async () => {
      await result.current.completeNewPassword("NuevaClave123!");
    });
    expect(result.current.state).toMatchObject({ status: "authenticated" });
    expect(respondNewPassword).toHaveBeenCalledWith(
      expect.anything(),
      "op@hubara.co",
      "SESS",
      "NuevaClave123!",
    );
  });

  it("boot con sesión vigente → authenticated sin pedir nada a Cognito", async () => {
    loaded = {
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresAt: Date.now() + 3_600_000,
      username: "op@hubara.co",
    };
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("authenticated"));
    expect(setAccessTokenMock).toHaveBeenCalledWith("ACC");
    expect(initiateAuth).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("signOut limpia sesión y vuelve a unauthenticated", async () => {
    loaded = {
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresAt: Date.now() + 3_600_000,
      username: "op@hubara.co",
    };
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("authenticated"));

    act(() => result.current.signOut());
    expect(result.current.state.status).toBe("unauthenticated");
    expect(clearSessionMock).toHaveBeenCalled();
    expect(setAccessTokenMock).toHaveBeenCalledWith(null);
  });
});
