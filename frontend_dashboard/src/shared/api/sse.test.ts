/**
 * Reconexión del SSE con token FRESCO (regresión prod 2026-07-16).
 *
 * El `EventSource` nativo reintenta solo con la MISMA URL del constructor —
 * o sea con el `access_token` que estaba vigente al montar. Cuando ese token
 * de Cognito vence (~1h) y la conexión se corta (deploy, blip de red, Android
 * suspendiendo el WebView), el reintento nativo recibe 401 y por spec el
 * browser NO vuelve a intentar: tiempo real muerto hasta recargar la página.
 * Síntoma observado: la web "refresca" solo por el fallback de 5 min y la app
 * móvil no refresca nada.
 *
 * `subscribeSse` debe manejar la reconexión a mano: ante `onerror`, cerrar el
 * stream y reabrirlo con backoff leyendo `getAccessToken()` EN CADA intento.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setAccessToken } from "../config/auth-token";
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

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setAccessToken(null);
});

describe("subscribeSse — reconexión con token fresco", () => {
  it("ante un error reabre la conexión con el token VIGENTE del store", () => {
    setAccessToken("token-viejo");
    subscribeSse("/api/dashboard/events", { onMessage: vi.fn() });

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain(
      "access_token=token-viejo",
    );

    // El AuthProvider refrescó el token mientras el stream vivía…
    setAccessToken("token-nuevo");
    // …y la conexión se corta (deploy / red / background del móvil).
    FakeEventSource.instances[0].onerror?.(new Event("error"));
    vi.runOnlyPendingTimers();

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].url).toContain(
      "access_token=token-nuevo",
    );
    // El stream muerto quedó cerrado (sin auto-retry nativo con URL vieja).
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("reporta el error al caller pero NO deja la conexión muerta", () => {
    const onError = vi.fn();
    subscribeSse("/x", { onMessage: vi.fn(), onError });

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledOnce();

    vi.runOnlyPendingTimers();
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("close() durante el backoff cancela el reintento pendiente", () => {
    const sub = subscribeSse("/x", { onMessage: vi.fn() });

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    sub.close();
    vi.runAllTimers();

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("el backoff crece entre reintentos fallidos y se resetea al abrir", () => {
    subscribeSse("/x", { onMessage: vi.fn() });

    // 1er error → reintento con el delay base.
    FakeEventSource.instances[0].onerror?.(new Event("error"));
    vi.advanceTimersByTime(1_000);
    expect(FakeEventSource.instances).toHaveLength(2);

    // 2do error consecutivo → el delay base ya no alcanza…
    FakeEventSource.instances[1].onerror?.(new Event("error"));
    vi.advanceTimersByTime(1_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    // …pero el doble sí.
    vi.advanceTimersByTime(1_000);
    expect(FakeEventSource.instances).toHaveLength(3);

    // Conexión exitosa → el próximo error vuelve al delay base.
    FakeEventSource.instances[2].onopen?.();
    FakeEventSource.instances[2].onerror?.(new Event("error"));
    vi.advanceTimersByTime(1_000);
    expect(FakeEventSource.instances).toHaveLength(4);
  });

  it("sigue entregando mensajes tras una reconexión", () => {
    const onMessage = vi.fn();
    const onOpen = vi.fn();
    subscribeSse<{ a: number }>("/x", { onMessage, onOpen });

    FakeEventSource.instances[0].onerror?.(new Event("error"));
    vi.runOnlyPendingTimers();

    const second = FakeEventSource.instances[1];
    second.onopen?.();
    second.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify({ a: 1 }) }),
    );

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onMessage).toHaveBeenCalledWith({ a: 1 });
  });
});
