/**
 * Tests de la máquina de auth móvil: la decisión de boot (pura) + los flujos
 * del hook (signIn éxito/error/contraseña-nueva, signOut) con Cognito mockeado.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import type { PersistedSession } from "@/shared/config";
import {
  decideBoot,
  isTransientRefreshError,
  useMobileAuth,
} from "./useMobileAuth";

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

const setUnauthorizedHandlerMock = vi.fn();

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
  setUnauthorizedHandler: (fn: (() => void) | null) =>
    setUnauthorizedHandlerMock(fn),
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
  it("PM2-A5: expiresAt implausible (reloj saltó hacia atrás) → refresh", () => {
    // expiresAt 48h en el futuro relativo a `now`: ningún token vive tanto.
    const skewed = { ...s, expiresAt: 48 * 60 * 60 * 1000 };
    expect(decideBoot(skewed, 500).action).toBe("refresh");
  });
});

describe("isTransientRefreshError", () => {
  it("red y throttling son transitorios (NO borran la sesión)", () => {
    expect(isTransientRefreshError("network")).toBe(true);
    expect(isTransientRefreshError("TooManyRequestsException")).toBe(true);
  });
  it("token revocado NO es transitorio", () => {
    expect(isTransientRefreshError("NotAuthorizedException")).toBe(false);
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

  it("PM2-A10: el email se normaliza (trim + minúsculas) antes de Cognito", async () => {
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
      await result.current.signIn("  Op@Hubara.CO ", "clave");
    });
    expect(initiateAuth).toHaveBeenCalledWith(
      expect.anything(),
      "op@hubara.co",
      "clave",
    );
  });

  it("PM2-A3: fallo de RED en el refresh del boot NO borra la sesión", async () => {
    loaded = {
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresAt: Date.now() - 1000, // vencido → boot va a refresh
      username: "op@hubara.co",
    };
    refresh.mockResolvedValue({
      kind: "error",
      code: "network",
      message: "Sin conexión con el servidor de login. Revisá la señal.",
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );
    // La sesión sobrevive (el retry con backoff la va a renovar solo).
    expect(clearSessionMock).not.toHaveBeenCalled();
  });

  it("refresh con token REVOCADO sí borra la sesión y pide re-login", async () => {
    loaded = {
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresAt: Date.now() - 1000,
      username: "op@hubara.co",
    };
    refresh.mockResolvedValue({
      kind: "error",
      code: "NotAuthorizedException",
      message: "Email o contraseña incorrectos.",
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );
    expect(clearSessionMock).toHaveBeenCalled();
    expect(setAccessTokenMock).toHaveBeenCalledWith(null);
  });

  it("PM2-A2: registra el handler de 401 del apiClient al montar", async () => {
    const { result, unmount } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));
    expect(setUnauthorizedHandlerMock).toHaveBeenCalledWith(expect.any(Function));
    unmount();
    expect(setUnauthorizedHandlerMock).toHaveBeenLastCalledWith(null);
  });

  it("PM2-A7: challenge expirado (NotAuthorizedException) → vuelta al login con mensaje honesto", async () => {
    initiateAuth.mockResolvedValue({
      kind: "new_password_required",
      session: "SESS",
      username: "op@hubara.co",
    });
    respondNewPassword.mockResolvedValue({
      kind: "error",
      code: "NotAuthorizedException",
      message: "Email o contraseña incorrectos.",
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));

    await act(async () => {
      await result.current.signIn("op@hubara.co", "temp");
    });
    await act(async () => {
      await result.current.completeNewPassword("NuevaClave123!");
    });
    expect(result.current.state.status).toBe("unauthenticated");
    expect(result.current.state).toMatchObject({
      error: expect.stringMatching(/expiró/i),
    });
  });

  it("PM2-A7: backToSignIn sale del challenge sin autenticar", async () => {
    initiateAuth.mockResolvedValue({
      kind: "new_password_required",
      session: "SESS",
      username: "op@hubara.co",
    });
    const { result } = renderHook(() => useMobileAuth());
    await waitFor(() => expect(result.current.state.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.signIn("op@hubara.co", "temp");
    });
    expect(result.current.state.status).toBe("new_password_required");
    act(() => result.current.backToSignIn());
    expect(result.current.state.status).toBe("unauthenticated");
  });

  it("PM2-A4: un refresh que resuelve DESPUÉS del signOut no resucita la sesión", async () => {
    loaded = {
      accessToken: "ACC",
      idToken: "ID",
      refreshToken: "REF",
      expiresAt: Date.now() - 1000, // vencido → boot dispara refresh
      username: "op@hubara.co",
    };
    let resolveRefresh: (v: unknown) => void = () => {};
    refresh.mockReturnValue(
      new Promise((r) => {
        resolveRefresh = r;
      }),
    );
    const { result } = renderHook(() => useMobileAuth());
    // El refresh del boot quedó en vuelo; el operador cierra sesión.
    act(() => result.current.signOut());
    loaded = null; // clearSession
    saveSessionMock.mockClear();
    // El refresh viejo resuelve con tokens — debe DESCARTARSE.
    await act(async () => {
      resolveRefresh({
        kind: "tokens",
        accessToken: "ZOMBIE",
        idToken: "Z",
        refreshToken: "Z",
        expiresIn: 3600,
      });
    });
    expect(result.current.state.status).toBe("unauthenticated");
    expect(saveSessionMock).not.toHaveBeenCalled();
  });
});
