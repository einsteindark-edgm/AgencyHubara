/**
 * Reconexión del SSE con credencial FRESCA (regresión prod 2026-07-16) + SEC-06.
 *
 * El `EventSource` nativo reintenta solo con la MISMA URL del constructor. Cuando
 * la credencial vence y la conexión se corta (deploy, blip de red, Android
 * suspendiendo el WebView), el reintento nativo recibe 401 y por spec el browser
 * NO vuelve a intentar: tiempo real muerto hasta recargar.
 *
 * SEC-06: ya NO ponemos el access-token en la URL. `subscribeSse` pide un TICKET
 * de corta vida (POST autenticado por header a `/api/dashboard/sse-ticket`) y lo
 * pasa por query `?ticket=`. Ante `onerror` reabre a mano mintando un ticket
 * fresco con el token vigente del store EN CADA intento.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAccessToken, setAccessToken } from "../config/auth-token";
import { apiClient } from "./client";
import { subscribeSse } from "./sse";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: ((err: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }
}

// Vacía la cola de microtasks (el `await apiClient.post` del mint) bajo fake timers.
async function flush(): Promise<void> {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  // El mint va por apiClient.post; lo mockeamos para devolver un ticket que
  // ENCODEA el token VIGENTE del store — así el `?ticket=` de la URL prueba con
  // qué credencial se reconectó (sin exponer el token real).
  vi.spyOn(apiClient, "post").mockImplementation(async () => {
    const token = getAccessToken() ?? "sin-token";
    return { ticket: `tkt-${token}` } as never;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  setAccessToken(null);
});

describe("subscribeSse — ticket SSE + reconexión fresca (SEC-06)", () => {
  it("abre el stream con un ticket (no con el access-token en la URL)", async () => {
    setAccessToken("token-viejo");
    subscribeSse("/api/dashboard/events", { onMessage: vi.fn() });
    await flush();

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("ticket=tkt-token-viejo");
    expect(FakeEventSource.instances[0].url).not.toContain("access_token");
  });

  it("ante un error reabre mintando un ticket con el token VIGENTE", async () => {
    setAccessToken("token-viejo");
    subscribeSse("/api/dashboard/events", { onMessage: vi.fn() });
    await flush();

    // El AuthProvider refrescó el token mientras el stream vivía…
    setAccessToken("token-nuevo");
    // …y la conexión se corta.
    FakeEventSource.instances[0].onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].url).toContain("ticket=tkt-token-nuevo");
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("reporta el error al caller pero NO deja la conexión muerta", async () => {
    const onError = vi.fn();
    subscribeSse("/x", { onMessage: vi.fn(), onError });
    await flush();

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledOnce();

    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("close() durante el backoff cancela el reintento pendiente", async () => {
    const sub = subscribeSse("/x", { onMessage: vi.fn() });
    await flush();

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    sub.close();
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("el backoff crece entre reintentos fallidos", async () => {
    subscribeSse("/x", { onMessage: vi.fn() });
    await flush();

    // 1er error → reintento con el delay base.
    FakeEventSource.instances[0].onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeEventSource.instances).toHaveLength(2);

    // 2do error consecutivo → el delay base ya no alcanza…
    FakeEventSource.instances[1].onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeEventSource.instances).toHaveLength(2);
    // …pero el doble sí.
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeEventSource.instances).toHaveLength(3);
  });

  it("sigue entregando mensajes tras una reconexión", async () => {
    const onMessage = vi.fn();
    const onOpen = vi.fn();
    subscribeSse<{ a: number }>("/x", { onMessage, onOpen });
    await flush();

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();

    const second = FakeEventSource.instances[1];
    second.onopen?.();
    second.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify({ a: 1 }) }),
    );

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onMessage).toHaveBeenCalledWith({ a: 1 });
  });
});
