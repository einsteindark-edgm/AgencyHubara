/**
 * Hooks de data-fetching del dominio "graphagents-run".
 *
 * Realtime (F1 — push-first): el progreso del run NO se pollea. El backend
 * (`runs/orchestrator.poll_loop`) pollea Conductor de la caja GraphAgents y
 * republica cada cambio al stream multiplexado del dashboard con
 * `bus.publish("graphagents", "run.<x>", id=run_id, payload={run_id, ...})`.
 * El plugin se suscribe con `useDashboardEvents("graphagents", ...)` (UNA
 * conexión app-level, como el resto de dominios) y, ante un evento cuyo
 * `payload.run_id` coincide, invalida el detalle del run — el `GET /runs/{id}`
 * trae el record autoritativo (status + events + result/awaiting). El endpoint
 * dedicado `/runs/{id}/events` existe para el viewer; el dashboard reusa el bus.
 *
 * `refetchInterval` numérico de 60s queda SOLO como red de seguridad (gate
 * R-REALTIME) por si el stream estuviera caído; la reconexión ya reconcilia vía
 * `useInvalidateOnReconnect`.
 *
 * Boundary: cada `apiClient.<verb>` va seguido de un `schema.parse(raw)` (gate
 * R-ZOD) — el `<T>` de compilación es documentación; el Zod es la ley.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback } from "react";

import { apiClient } from "@/shared/api/client";
import {
  useDashboardEvents,
  useInvalidateOnReconnect,
} from "@/shared/api/events-context";

import {
  agentsListResponseSchema,
  approveResponseSchema,
  runRecordSchema,
  triggerRunResponseSchema,
} from "./contracts";
import { graphagentsRunKeys } from "./keys";
import type { AgentOption, RunRecord } from "./model";

/** Red de seguridad (gate R-REALTIME ≥ 60s) — el mecanismo primario es el push. */
const RUN_FALLBACK_REFETCH_MS = 60_000;

async function fetchAgents(signal?: AbortSignal): Promise<AgentOption[]> {
  const raw = await apiClient.get<unknown>("/api/graphagents/agents", {
    signal,
  });
  return agentsListResponseSchema.parse(raw);
}

async function fetchRun(runId: string, signal?: AbortSignal): Promise<RunRecord> {
  const raw = await apiClient.get<unknown>(
    `/api/graphagents/runs/${runId}`,
    { signal },
  );
  return runRecordSchema.parse(raw);
}

/** Catálogo de agentes del selector. Estático del lado del backend → sin polling. */
export function useAgents() {
  return useQuery({
    queryKey: graphagentsRunKeys.agents(),
    queryFn: ({ signal }) => fetchAgents(signal),
  });
}

/**
 * Detalle de un run. `enabled` se apaga si `runId` es null (no dispara fetch al
 * string vacío). La actualización primaria llega por `useGraphAgentsRunEvents`;
 * el interval es solo red de seguridad.
 */
export function useRun(runId: string | null) {
  return useQuery({
    queryKey: graphagentsRunKeys.detail(runId ?? ""),
    queryFn: ({ signal }) => fetchRun(runId!, signal),
    enabled: !!runId,
    refetchInterval: RUN_FALLBACK_REFETCH_MS,
  });
}

/**
 * Dispara un run — `POST /api/graphagents/runs`. Devuelve el `run_id` validado
 * (el caller lo guarda como selección activa para montar `useRun`).
 */
export function useTriggerRun() {
  return useMutation({
    mutationFn: async (vars: { agent: string; input: unknown }) => {
      const raw = await apiClient.post<unknown>("/api/graphagents/runs", {
        agent: vars.agent,
        input: vars.input,
      });
      return triggerRunResponseSchema.parse(raw).run_id;
    },
  });
}

/**
 * Resume el HITL — `POST /api/graphagents/runs/{run_id}/approve`. Tras resolver,
 * invalida el detalle del run (el backend lo moverá de `awaiting_approval` y el
 * stream empujará el cambio, pero la invalidación inmediata evita el lag visual).
 */
export function useApproveRun(runId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { approved: boolean; by: string }) => {
      const raw = await apiClient.post<unknown>(
        `/api/graphagents/runs/${runId}/approve`,
        { decision: { approved: vars.approved, by: vars.by } },
      );
      return approveResponseSchema.parse(raw).status;
    },
    onSuccess: () => {
      if (runId) {
        qc.invalidateQueries({ queryKey: graphagentsRunKeys.detail(runId) });
      }
    },
  });
}

/**
 * Suscripción del plugin al stream multiplexado del dashboard para UN run.
 * Montar UNA vez en la sección (lo hace GraphAgentsSection). Ante cualquier
 * evento `graphagents` cuyo `payload.run_id` coincide con el run observado,
 * invalida su detalle → el `useRun` refetchea el record autoritativo. La
 * reconexión invalida el detalle por si se perdieron eventos en el gap.
 */
export function useGraphAgentsRunEvents(runId: string | null) {
  const qc = useQueryClient();

  const invalidate = useCallback(() => {
    if (runId) {
      qc.invalidateQueries({ queryKey: graphagentsRunKeys.detail(runId) });
    }
  }, [qc, runId]);

  useDashboardEvents(
    "graphagents",
    useCallback(
      (event) => {
        if (!runId) return;
        // El bus publica `id=run_id` y `payload.run_id`; matcheamos por
        // cualquiera de los dos (defensa ante un publish sin `id`).
        const payload = event.payload as { run_id?: string } | undefined;
        const eventRunId = event.id ?? payload?.run_id;
        if (eventRunId === runId) {
          invalidate();
        }
      },
      [runId, invalidate],
    ),
  );

  useInvalidateOnReconnect(invalidate);
}
