/**
 * Smoke test del client. Verifica que:
 *   - construye la URL con la base de env
 *   - parsea JSON cuando el content-type es application/json
 *   - tira `ApiError` con status + body en respuestas !ok
 *
 * No probamos `request()` directamente (no exportado): testeamos la fachada `apiClient`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiClient, ApiError } from "./client";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
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
});
