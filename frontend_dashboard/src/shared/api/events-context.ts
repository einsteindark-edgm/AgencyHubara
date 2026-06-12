/**
 * Contexto + hooks del event stream del dashboard (F1, auditoría 2026-06-10).
 *
 * Separado del provider (`events.tsx`) para que cada archivo exporte una sola
 * "clase" de cosa (regla react-refresh/only-export-components): acá los hooks
 * y tipos sin JSX; el componente EventStreamProvider vive en events.tsx.
 */

import { createContext, useContext, useEffect, useRef } from "react";
import { z } from "zod";

export const dashboardEventSchema = z.object({
  domain: z.string(),
  type: z.string(),
  id: z.string().nullable().optional(),
  ts_ms: z.number().optional(),
  payload: z.unknown().optional(),
});

export type DashboardEvent = z.infer<typeof dashboardEventSchema>;

export type EventStreamState = "connecting" | "open" | "reconnecting";

export type DashboardEventHandler = (event: DashboardEvent) => void;

export interface EventStreamContextValue {
  state: EventStreamState;
  /** Sube en cada (re)conexión. Los entity-hooks invalidan al detectar salto. */
  epoch: number;
  subscribe: (domain: string, handler: DashboardEventHandler) => () => void;
}

export const EventStreamContext =
  createContext<EventStreamContextValue | null>(null);

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
