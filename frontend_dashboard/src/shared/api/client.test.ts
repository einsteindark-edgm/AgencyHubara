/**
 * Smoke test del client. Verifica que:
 *   - construye la URL con la base de env
 *   - parsea JSON cuando el content-type es application/json
 *   - tira `ApiError` con status + body en respuestas !ok
 *   - adjunta `Authorization: Bearer <token>` cuando hay sesión (token-store)
 *
 * No probamos `request()` directamente (no exportado): testeamos la fachada `apiClient`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setAccessToken } from "../config/auth-token";
import { apiClient, ApiError } from "./client";

describe("postExternal en WebViews viejos (Android 11 stock = Chrome 86)", () => {
  it("funciona sin AbortSignal.timeout (fallback AbortController)", async () => {
    // Regresión device real 2026-07-15: el System WebView de fábrica de
    // Android 11 no tiene AbortSignal.timeout (Chrome 103+) — el login moría
    // con TypeError en el primer tap.
    const original = AbortSignal.timeout;
    // @ts-expect-error — simular el WebView viejo
    delete AbortSignal.timeout;
    try {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
      );
      const { status, data } = await apiClient.postExternal(
        "https://idp.example/",
        { a: 1 },
        { "Content-Type": "application/json" },
      );
      expect(status).toBe(200);
      expect(data).toEqual({ ok: 1 });
      // El fetch igual recibió UNA señal abortable (el fallback).
      const init = fetchMock.mock.calls[0][1] as RequestInit;
      expect(init.signal).toBeInstanceOf(AbortSignal);
    } finally {
      AbortSignal.timeout = original;
    }
  });
});

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  setAccessToken(null); // no filtrar el token entre tests
});

describe("apiClient", () => {
  it("prefixes paths with VITE_API_URL and parses JSON", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const data = await apiClient.get<{ ok: boolean }>("/health");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/health$/);
    expect(url).toContain("http://");
    expect(data).toEqual({ ok: true });
  });

  it("throws ApiError with status + body on non-2xx", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "boom" }), {
        status: 500,
        headers: { "content-type": "application/json" },
      }),
    );

    const err = await apiClient.get("/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      name: "ApiError",
      status: 500,
      body: { error: "boom" },
    });
  });

  it("serializes body as JSON on POST", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await apiClient.post("/echo", { hello: "world" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ hello: "world" }));
    expect((init.headers as Headers).get("content-type")).toBe(
      "application/json",
    );
  });

  it("attaches Authorization: Bearer when a session token is set", async () => {
    setAccessToken("fake-access-token");
    fetchMock.mockResolvedValueOnce(
      new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await apiClient.get("/secure");

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Headers).get("authorization")).toBe(
      "Bearer fake-access-token",
    );
  });

  it("omits Authorization when there is no session token", async () => {
    setAccessToken(null);
    fetchMock.mockResolvedValueOnce(
      new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await apiClient.get("/public");

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Headers).get("authorization")).toBeNull();
  });
});
