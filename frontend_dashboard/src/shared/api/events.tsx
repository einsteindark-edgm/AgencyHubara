/**
 * Event stream del dashboard — cliente del SSE multiplexado
 * `/api/dashboard/events` (F1, auditoría 2026-06-10).
 *
 * UNA conexión EventSource por app (la monta `EventStreamProvider` en
 * app/providers) que reparte eventos `{domain, type, id?, payload?}` a
 * handlers registrados por dominio. Los plugins NO abren sus propios
 * streams ni pollean: declaran `useDashboardEvents("<dominio>", handler)`
 * en su entity y traducen eventos → invalidaciones/`setQueryData`.
 *
 * Política (CLAUDE.md frontend §estado):
 *   - realtime por push; `refetchInterval` queda SOLO como fallback lento
 *     (≥60s) o function-form acotado a un run activo (patrón catalog).
 *   - reconexión: la maneja el propio EventSource del browser; acá solo
 *     exponemos `state` (para el StatusBar) y `epoch` (sube en cada open)
 *     para que `useInvalidateOnReconnect` reconcilie los gaps.
 *
 * FSD: shared NO conoce dominios — `domain` es un string opaco que cada
 * plugin elige (espejo del bus backend en src/platform/events).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { z } from "zod";

import { subscribeSse } from "./sse";

const dashboardEventSchema = z.object({
  domain: z.string(),
  type: z.string(),
  id: z.string().nullable().optional(),
  ts_ms: z.number().optional(),
  payload: z.unknown().optional(),
});

export type DashboardEvent = z.infer<typeof dashboardEventSchema>;

export type EventStreamState = "connecting" | "open" | "reconnecting";

type DashboardEventHandler = (event: DashboardEvent) => void;

interface EventStreamContextValue {
  state: EventStreamState;
  /** Sube en cada (re)conexión. Los entity-hooks invalidan al detectar salto. */
  epoch: number;
  subscribe: (domain: string, handler: DashboardEventHandler) => () => void;
}

const EventStreamContext = createContext<EventStreamContextValue | null>(null);

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<EventStreamState>("connecting");
  const [epoch, setEpoch] = useState(0);
  const handlersRef = useRef(new Map<string, Set<DashboardEventHandler>>());

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
        setEpoch((e) => e + 1);
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

function useEventStreamContext(): EventStreamContextValue {
  const ctx = useContext(EventStreamContext);
  if (ctx === null) {
    throw new Error(
      "useDashboardEvents()/useEventStreamState() fuera de <EventStreamProvider> " +
        "— se monta una sola vez en app/providers.",
    );
  }
  return ctx;
}

/**
 * Registra un handler para un dominio. El handler vive en un ref: cambiarlo
 * NO re-suscribe (evita churn del registry en cada render del caller).
 */
export function useDashboardEvents(
  domain: string,
  handler: DashboardEventHandler,
): void {
  const { subscribe } = useEventStreamContext();
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });
  useEffect(
    () => subscribe(domain, (event) => handlerRef.current(event)),
    [subscribe, domain],
  );
}

/** Estado de la conexión — lo consume el StatusBar del shell. */
export function useEventStreamState(): EventStreamState {
  return useEventStreamContext().state;
}

/**
 * Ejecuta `invalidate` cuando el stream RE-conecta (gap de eventos perdidos
 * → reconciliar refetcheando). No dispara en el primer open ni al montar.
 */
export function useInvalidateOnReconnect(invalidate: () => void): void {
  const { epoch } = useEventStreamContext();
  const seen = useRef(epoch);
  useEffect(() => {
    if (epoch !== seen.current) {
      seen.current = epoch;
      invalidate();
    }
  }, [epoch, invalidate]);
}
