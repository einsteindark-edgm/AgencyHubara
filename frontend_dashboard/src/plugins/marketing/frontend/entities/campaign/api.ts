/**
 * Hooks de fetching/mutación de campañas contra `/api/marketing/*`.
 *
 * Todo boundary HTTP: `apiClient` del SDK + `{signal}` (cancela requests en
 * vuelo al cambiar de sección) + Zod `.parse()` (contract drift truena
 * temprano) + mapper snake→camel.
 *
 * Realtime: el backend de marketing no publica eventos SSE todavía — la lista
 * usa el refetch numérico ≥60s permitido como red de seguridad (regla #2)
 * para reflejar transiciones draft→sending→sent que hace el workflow.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendCampaignSchema,
  backendCampaignsResponseSchema,
  backendCampaignStatsSchema,
  backendSendResponseSchema,
  backendTestSendResponseSchema,
  type BackendCampaign,
  type BackendCampaignStats,
} from "./contracts";
import { campaignKeys } from "./keys";
import type {
  Campaign,
  CampaignGoal,
  CampaignPatch,
  CampaignSendStarted,
  CampaignStats,
  CampaignStatus,
} from "./model";

/* ── Mappers backend → dominio ──────────────────────────────────────────── */

/** Narrowing defensivo: un status fuera del vocabulario cae a "draft". */
function asStatus(s: string): CampaignStatus {
  if (
    s === "draft" ||
    s === "scheduled" ||
    s === "sending" ||
    s === "sent" ||
    s === "failed"
  )
    return s;
  return "draft";
}

function asGoal(g: string): CampaignGoal {
  if (g === "discount_general" || g === "discount_product" || g === "launch")
    return g;
  return "";
}

export function mapBackendCampaign(b: BackendCampaign): Campaign {
  return {
    id: b.id,
    name: b.name,
    status: asStatus(b.status),
    goal: asGoal(b.goal),
    percent: b.percent,
    couponCode: b.coupon_code,
    validUntil: b.valid_until,
    productHandle: b.product_handle,
    segments: b.segments,
    message: b.message,
    templateName: b.template_name,
    scheduleAtMs: b.schedule_at_ms,
    createdAtMs: b.created_at_ms,
    updatedAtMs: b.updated_at_ms,
    sentAtMs: b.sent_at_ms,
    sendResult:
      b.send_result === null
        ? null
        : {
            planned: b.send_result.planned,
            sent: b.send_result.sent,
            failed: b.send_result.failed,
            skipped: b.send_result.skipped.map((s) => ({
              sessionId: s.session_id,
              reason: s.reason,
            })),
            unitCostUsdMicros: b.send_result.unit_cost_usd_micros,
            spentUsdMicros: b.send_result.spent_usd_micros,
          },
    testSends: b.test_sends.map((t) => ({
      phone: t.phone,
      atMs: t.at_ms,
      waMessageId: t.wa_message_id,
    })),
    excludedSessionIds: b.excluded_session_ids,
    extraSessionIds: b.extra_session_ids,
  };
}

export function mapBackendStats(b: BackendCampaignStats): CampaignStats {
  return {
    campaignId: b.campaign_id,
    status: asStatus(b.status),
    planned: b.planned,
    sent: b.sent,
    failedCount: b.failed_count,
    skippedCount: b.skipped_count,
    unitCostUsdMicros: b.unit_cost_usd_micros,
    spentUsdMicros: b.spent_usd_micros,
    replied: b.replied,
    attributedOrders: b.attributed_orders,
    attributedRevenueCop: b.attributed_revenue_cop,
  };
}

/** Patch camelCase → body snake_case del PUT (solo campos presentes). */
export function patchToBody(patch: CampaignPatch): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (patch.name !== undefined) body.name = patch.name;
  if (patch.goal !== undefined) body.goal = patch.goal;
  if (patch.percent !== undefined) body.percent = patch.percent;
  if (patch.couponCode !== undefined) body.coupon_code = patch.couponCode;
  if (patch.validUntil !== undefined) body.valid_until = patch.validUntil;
  if (patch.productHandle !== undefined) body.product_handle = patch.productHandle;
  if (patch.segments !== undefined) body.segments = patch.segments;
  if (patch.message !== undefined) body.message = patch.message;
  if (patch.excludedSessionIds !== undefined)
    body.excluded_session_ids = patch.excludedSessionIds;
  if (patch.extraSessionIds !== undefined)
    body.extra_session_ids = patch.extraSessionIds;
  return body;
}

/* ── Queries ────────────────────────────────────────────────────────────── */

/** Todas las campañas del vault (`_campaigns/`), más nueva primero. */
export function useCampaigns() {
  return useQuery<Campaign[]>({
    queryKey: campaignKeys.list(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/marketing/campaigns", {
        signal,
      });
      const parsed = backendCampaignsResponseSchema.parse(raw);
      return parsed.campaigns
        .map(mapBackendCampaign)
        .sort((a, b) => b.createdAtMs - a.createdAtMs);
    },
    staleTime: 30_000,
    // Red de seguridad (numérico ≥ 60s permitido): el CampaignSendWorkflow
    // muta status/send_result fuera del API — sin esto una campaña "sending"
    // jamás pasaría a "sent" en pantalla.
    refetchInterval: 60_000,
  });
}

/** Stats de envío + atribución — solo tiene sentido para campañas enviadas
 *  (el caller controla `enabled`). */
export function useCampaignStats(campaignId: string, enabled = true) {
  return useQuery<CampaignStats>({
    queryKey: campaignKeys.stats(campaignId),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/stats`,
        { signal },
      );
      return mapBackendStats(backendCampaignStatsSchema.parse(raw));
    },
    staleTime: 30_000,
    enabled: enabled && Boolean(campaignId),
  });
}

/* ── Mutations ──────────────────────────────────────────────────────────── */

/** POST /campaigns — crea un draft; el caller decide qué seleccionar. */
export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation<Campaign, Error, string | void>({
    mutationFn: async (name) => {
      const raw = await apiClient.post<unknown>(
        "/api/marketing/campaigns",
        typeof name === "string" && name ? { name } : {},
      );
      return mapBackendCampaign(backendCampaignSchema.parse(raw));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: campaignKeys.list() });
    },
  });
}

/** PUT /campaigns/{id} — guardado explícito del builder (blur/botón).
 *  409 si la campaña ya no es editable (sent/sending/failed). */
export function useUpdateCampaign(campaignId: string) {
  const qc = useQueryClient();
  return useMutation<Campaign, Error, CampaignPatch>({
    mutationFn: async (patch) => {
      const raw = await apiClient.put<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}`,
        patchToBody(patch),
      );
      return mapBackendCampaign(backendCampaignSchema.parse(raw));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: campaignKeys.list() });
    },
  });
}

/** POST /campaigns/{id}/send — `null` envía YA, un epoch-ms futuro programa
 *  (422 si falta objetivo/mensaje/audiencia o la fecha no es futura). */
export function useSendCampaign(campaignId: string) {
  const qc = useQueryClient();
  return useMutation<CampaignSendStarted, Error, number | null>({
    mutationFn: async (scheduleAtMs) => {
      const raw = await apiClient.post<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/send`,
        scheduleAtMs === null ? {} : { schedule_at_ms: scheduleAtMs },
      );
      const parsed = backendSendResponseSchema.parse(raw);
      return {
        workflowId: parsed.workflow_id,
        runId: parsed.run_id,
        scheduled: parsed.scheduled,
      };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: campaignKeys.list() });
    },
  });
}

/** POST /campaigns/{id}/cancel — aborta una campaña PROGRAMADA: cancela el
 *  workflow encolado y la devuelve a borrador (re-programar = enviar de
 *  nuevo). 409 si la campaña no está programada. */
export function useCancelCampaign(campaignId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await apiClient.post<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/cancel`,
        {},
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: campaignKeys.list() });
    },
  });
}

/** POST /campaigns/{id}/test — prueba a UN número del operador. 404 con
 *  detail legible si el número nunca chateó con el bot (superficie eso). */
export function useTestSend(campaignId: string) {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean; sessionId: string }, Error, string>({
    mutationFn: async (phone) => {
      const raw = await apiClient.post<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/test`,
        { phone },
      );
      const parsed = backendTestSendResponseSchema.parse(raw);
      return { ok: parsed.ok, sessionId: parsed.session_id };
    },
    // El backend appendea a test_sends → la lista es la fuente del historial.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: campaignKeys.list() });
    },
  });
}
