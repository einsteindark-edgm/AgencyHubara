/**
 * Hooks de fetching de la audiencia contra `/api/marketing/*`.
 *
 * Todo boundary HTTP: `apiClient` del SDK + `{signal}` + Zod `.parse()` +
 * mapper snake→camel. Read-only (no hay mutations sobre la audiencia).
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendAudienceConversationSchema,
  backendCampaignAudienceSchema,
  type BackendAudienceConversation,
  type BackendCampaignAudience,
} from "./contracts";
import { audienceKeys } from "./keys";
import type {
  AudienceConversation,
  CampaignAudience,
  ConversationRole,
} from "./model";

/* ── Mappers backend → dominio ──────────────────────────────────────────── */

/** Narrowing defensivo: un role fuera del vocabulario cae a "assistant"
 *  (burbuja del negocio — jamás atribuirle al cliente algo que no dijo). */
function asRole(r: string): ConversationRole {
  return r === "user" ? "user" : "assistant";
}

export function mapBackendAudience(
  b: BackendCampaignAudience,
): CampaignAudience {
  return {
    recipients: b.recipients.map((r) => ({
      sessionId: r.session_id,
      phone: r.phone,
      customerName: r.customer_name,
      segment: r.segment,
    })),
    skipped: b.skipped.map((s) => ({
      sessionId: s.session_id,
      phone: s.phone,
      reason: s.reason,
    })),
    total: b.total,
  };
}

export function mapBackendConversation(
  b: BackendAudienceConversation,
): AudienceConversation {
  return {
    sessionId: b.session_id,
    messages: b.messages.map((m) => ({
      role: asRole(m.role),
      kind: m.kind,
      content: m.content,
      timestamp: m.timestamp,
    })),
  };
}

/* ── Queries ────────────────────────────────────────────────────────────── */

/** Audiencia REAL de la campaña GUARDADA (misma lógica del envío; quiet
 *  hours se evalúa recién al disparo). `updatedAtMs` mantiene el dato fresco
 *  tras commitear segmentos (ver keys.ts). */
export function useCampaignAudience(campaignId: string, updatedAtMs = 0) {
  return useQuery<CampaignAudience>({
    queryKey: audienceKeys.forCampaign(campaignId, updatedAtMs),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/audience`,
        { signal },
      );
      return mapBackendAudience(backendCampaignAudienceSchema.parse(raw));
    },
    staleTime: 30_000,
    enabled: Boolean(campaignId),
  });
}

/** Últimos 60 mensajes de una sesión, read-only. `null` = visor cerrado
 *  (query deshabilitada — no pegarle al backend sin sesión elegida). */
export function useAudienceConversation(sessionId: string | null) {
  return useQuery<AudienceConversation>({
    queryKey: audienceKeys.conversation(sessionId ?? ""),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/marketing/audience/${encodeURIComponent(sessionId ?? "")}/conversation`,
        { signal },
      );
      return mapBackendConversation(backendAudienceConversationSchema.parse(raw));
    },
    staleTime: 30_000,
    enabled: sessionId !== null,
  });
}
