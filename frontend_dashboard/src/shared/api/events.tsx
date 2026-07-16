/**
 * Event stream del dashboard — cliente del SSE multiplexado
 * `/api/dashboard/events` (F1, auditoría 2026-06-10).
 *
 * UNA conexión EventSource por app (la monta este provider desde
 * app/providers) que reparte eventos `{domain, type, id?, payload?}` a
 * handlers registrados por dominio. Los plugins NO abren sus propios
 * streams ni pollean: declaran `useDashboardEvents("<dominio>", handler)`
 * en su entity y traducen eventos → invalidaciones/`setQueryData`.
 *
 * Política (CLAUDE.md frontend §estado):
 *   - realtime por push; `refetchInterval` queda SOLO como fallback lento
 *     (≥60s) o function-form acotado a un run activo (patrón catalog) —
 *     gate: test_realtime_policy.arch.test.ts.
 *   - reconexión: la maneja `subscribeSse` (manual, con token fresco y
 *     backoff — el auto-retry nativo moría con 401 al vencer el token); acá
 *     solo exponemos `state` (StatusBar) y `epoch` (useInvalidateOnReconnect).
 *
 * FSD: shared NO conoce dominios — `domain` es un string opaco que cada
 * plugin elige (espejo del bus backend en src/platform/events). Hooks y
 * tipos viven en `events-context.ts` (regla only-export-components).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  EventStreamContext,
  dashboardEventSchema,
  type DashboardEventHandler,
  type EventStreamState,
} from "./events-context";
import { subscribeSse } from "./sse";

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<EventStreamState>("connecting");
  const [epoch, setEpoch] = useState(0);
  const handlersRef = useRef(new Map<string, Set<DashboardEventHandler>>());
  // El epoch sube solo en RE-conexiones: el primer open NO debe invalidar
  // (las queries acaban de fetchear al montar — premortem 2026-06-11).
  const hasOpenedRef = useRef(false);

  const subscribe = useCallback(
    (domain: string, handler: DashboardEventHandler) => {
      const byDomain = handlersRef.current;
      let set = byDomain.get(domain);
      if (set === undefined) {
        set = new Set();
        byDomain.set(domain, set);
      }
      set.add(handler);
      return () => {
        byDomain.get(domain)?.delete(handler);
      };
    },
    [],
  );

  useEffect(() => {
    // jsdom (vitest) no implementa EventSource; en tests el provider queda
    // en "connecting" y los handlers simplemente no reciben eventos.
    if (typeof EventSource === "undefined") return;
    const sub = subscribeSse<unknown>("/api/dashboard/events", {
      onOpen: () => {
        setState("open");
        if (hasOpenedRef.current) {
          setEpoch((e) => e + 1);
        } else {
          hasOpenedRef.current = true;
        }
      },
      onError: () => {
        // EventSource reintenta solo; nosotros solo reflejamos el estado.
        setState("reconnecting");
      },
      onMessage: (raw) => {
        const parsed = dashboardEventSchema.safeParse(raw);
        if (!parsed.success) {
          console.warn("dashboard event inválido", parsed.error);
          return;
        }
        const event = parsed.data;
        handlersRef.current.get(event.domain)?.forEach((handler) => {
          try {
            handler(event);
          } catch (err) {
            // Un handler roto no puede tumbar el reparto a los demás.
            console.error(`handler de eventos '${event.domain}' falló`, err);
          }
        });
      },
    });
    return () => sub.close();
  }, []);

  const value = useMemo(
    () => ({ state, epoch, subscribe }),
    [state, epoch, subscribe],
  );

  return (
    <EventStreamContext.Provider value={value}>
      {children}
    </EventStreamContext.Provider>
  );
}
