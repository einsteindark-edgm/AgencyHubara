/**
 * Hooks de la conexión a Meta — estado de la conexión provisionada + insights reales.
 *
 * Server state SOLO en TanStack Query (regla 1). Single-tenant (2026-07-09): el
 * token se provisiona server-side (SSM) — no hay login URL ni disconnect; la UI
 * solo LEE el estado y consume datos.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import {
  backendMetaAnalysisInputSchema,
  backendMetaInsightsSchema,
  backendMetaStatusSchema,
} from "./contracts";
import { metaConnectionKeys } from "./keys";
import type { MetaConnection, MetaInsights, MetaInsightsParams } from "./model";

const DISCONNECTED: MetaConnection = {
  connected: false,
  accountId: null,
  accountName: null,
  scopes: [],
  expiresAt: null,
  expired: false,
  canManage: false,
};

export function useMetaConnection() {
  return useQuery<MetaConnection>({
    queryKey: metaConnectionKeys.status(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/ads/meta/status", { signal });
      const parsed = backendMetaStatusSchema.parse(raw);
      if (!parsed.connected) return DISCONNECTED;
      return {
        connected: true,
        accountId: parsed.account_id,
        accountName: parsed.account_name,
        scopes: parsed.scopes,
        expiresAt: parsed.expires_at,
        expired: parsed.expired,
        canManage: parsed.can_manage,
      };
    },
    staleTime: 30_000,
  });
}

function insightsQuery(p: MetaInsightsParams): string {
  const q = new URLSearchParams();
  if (p.since && p.until) {
    q.set("since", p.since);
    q.set("until", p.until);
  } else if (p.days != null) {
    q.set("days", String(p.days));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function useMetaInsights(params: MetaInsightsParams, enabled = true) {
  return useQuery<MetaInsights>({
    queryKey: metaConnectionKeys.insights(params),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/ads/meta/insights${insightsQuery(params)}`,
        { signal },
      );
      const p = backendMetaInsightsSchema.parse(raw);
      return {
        connected: p.connected,
        accountId: p.account_id ?? null,
        accountName: p.account_name ?? null,
        since: p.since ?? null,
        until: p.until ?? null,
        campaigns: p.campaigns.map((c) => ({
          campaignId: c.campaign_id,
          name: c.name,
          status: c.status,
          objective: c.objective,
          spend: c.spend,
          impressions: c.impressions,
          reach: c.reach,
          clicks: c.clicks,
          messagingConversationsStarted: c.messaging_conversations_started,
        })),
      };
    },
    staleTime: 30_000,
    enabled,
  });
}

/**
 * Trae el JSON de entrada REAL para el pod `ads-analytics` (datos de Graph en el
 * shape que el agente consume). Lo usa el form de "Analizar con IA" para pre-cargar
 * con tus campañas reales en vez del seed de ejemplo. `enabled` = solo si conectado.
 */
export function useMetaAnalysisInput(enabled = true) {
  return useQuery<unknown>({
    queryKey: metaConnectionKeys.analysisInput(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/ads/meta/analysis-input?days=14", { signal });
      return backendMetaAnalysisInputSchema.parse(raw);
    },
    staleTime: 30_000,
    enabled,
  });
}

/**
 * Gestión: pausa/activa una campaña (acción OUTWARD sobre Meta). El HITL es el
 * confirm de dos pasos en la UI (regla 6); esta mutation solo la dispara tras
 * confirmar. Invalida los insights para reflejar el nuevo status.
 */
export function useSetCampaignStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { campaignId: string; status: "PAUSED" | "ACTIVE" }) =>
      apiClient.post<unknown>(
        `/api/ads/meta/campaigns/${encodeURIComponent(v.campaignId)}/status`,
        { status: v.status },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: metaConnectionKeys.all });
    },
  });
}
