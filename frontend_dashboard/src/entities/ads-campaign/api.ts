/**
 * Hooks de fetching de campañas y conversaciones atribuidas.
 *
 * `useAdsCampaigns` y `useAttributedConversations` ahora hacen fetch real
 * contra el backend (`/api/chats/ads/*` — los endpoints viven en chats
 * porque las campañas se derivan del estado clasificado por el ingest
 * WhatsApp). Los responses se validan en el boundary con Zod y se mapean
 * al modelo de dominio.
 *
 * `useDailySeries` sigue retornando mock — la serie diaria aún no está
 * implementada server-side y requiere un rollup temporal que no es parte
 * del scope de esta integración inicial.
 *
 * Datos faltantes (spend, revenue, status Meta, name de cliente, etc.)
 * viajan como `null` y los components los pintan con "—" + icono
 * `dataPending`.
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import {
  backendAdsCampaignsResponseSchema,
  backendAttributedConversationsResponseSchema,
  type BackendAdsCampaign,
  type BackendAttributedConversation,
} from "./contracts";
import { adsCampaignKeys } from "./keys";
import {
  DAILY_SERIES,
  type AdsCampaign,
  type AdsDailyPoint,
  type AdsState,
  type AttributedConversation,
  type AvatarColor,
  type CampaignStatus,
  type CampaignTendency,
} from "./model";

const AVATAR_COLORS: AvatarColor[] = ["a", "b", "c", "d", "e", "f"];

/* ── Helpers de mapping backend → dominio ────────────────────────────────── */

function formatDateRange(firstMs: number | null, lastMs: number | null): string {
  if (firstMs === null && lastMs === null) return "—";
  const fmt = (ms: number | null) =>
    ms === null
      ? "—"
      : new Date(ms).toLocaleDateString("es-CO", {
          day: "numeric",
          month: "short",
        });
  return `${fmt(firstMs)} → ${fmt(lastMs)}`;
}

function formatRelativeMs(ms: number | null): string | null {
  if (ms === null) return null;
  const d = new Date(ms);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return `Hoy ${d.getHours().toString().padStart(2, "0")}:${d
      .getMinutes()
      .toString()
      .padStart(2, "0")}`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday =
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate();
  if (isYesterday) {
    return `Ayer ${d.getHours().toString().padStart(2, "0")}:${d
      .getMinutes()
      .toString()
      .padStart(2, "0")}`;
  }
  return d.toLocaleDateString("es-CO", { day: "numeric", month: "short" });
}

/** Iniciales 2-letras del nombre, o últimos 2 dígitos del phone si name=null. */
function deriveInitials(name: string | null, phoneNumber: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/).slice(0, 2);
    return parts
      .map((p) => p[0]?.toUpperCase() ?? "")
      .join("")
      .padEnd(2, "·");
  }
  // Fallback determinístico desde el phone — últimos 2 dígitos.
  const digits = phoneNumber.replace(/\D/g, "");
  return digits.slice(-2) || "··";
}

/** Color determinístico desde el phone — hash trivial. */
function deriveColor(phoneNumber: string): AvatarColor {
  const digits = phoneNumber.replace(/\D/g, "");
  let h = 0;
  for (const ch of digits) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

/** Backend `status` string → enum del dominio. Tolerante a valores raros. */
function asCampaignStatus(s: string | null): CampaignStatus | null {
  if (s === "active" || s === "paused") return s;
  return null;
}

function asCampaignTendency(s: string | null): CampaignTendency | null {
  if (s === "up" || s === "flat" || s === "down") return s;
  return null;
}

function asAdsState(s: string | null): AdsState | null {
  if (
    s === "no_reply" ||
    s === "nuevo" ||
    s === "activo" ||
    s === "calificado" ||
    s === "cotizado" ||
    s === "ganado" ||
    s === "perdido"
  )
    return s;
  return null;
}

function mapBackendCampaign(b: BackendAdsCampaign): AdsCampaign {
  return {
    id: b.id,
    name: b.name,
    started: b.started,
    dates: formatDateRange(b.first_seen_ms, b.last_seen_ms),
    status: asCampaignStatus(b.status),
    objective: b.objective,
    placement: b.placement,
    audience: b.audience,
    daysRun: b.days_run,
    metaCampaignId: b.meta_campaign_id,
    adSet: b.ad_set,
    creativeTitle: b.creative_title,
    template: b.template,
    spend: b.spend,
    impressions: b.impressions,
    reach: b.reach,
    clicks: b.clicks,
    // Counts por estado conversacional — backend devuelve dict con 7 keys
    // (clasificador heurístico via `classify_state`). Habilita
    // `AdsStateDistribution` y partial `AdsFunnel`.
    conversations: b.conversations,
    revenue: b.revenue,
    avgTicket: b.avg_ticket,
    firstResp: b.first_resp,
    tendency: asCampaignTendency(b.tendency),
  };
}

function mapBackendConversation(
  b: BackendAttributedConversation,
): AttributedConversation {
  return {
    id: b.id,
    episodeId: b.episode_id,
    short: deriveInitials(b.name, b.phone_number),
    color: deriveColor(b.phone_number),
    started: formatRelativeMs(b.started_at_ms) ?? "—",
    msgs: b.msgs_count,
    name: b.name,
    city: b.city,
    agent: b.agent,
    state: asAdsState(b.state),
    value: b.value,
    lastMsg: formatRelativeMs(b.last_msg_at_ms),
    ad: b.ad_headline,
  };
}

/* ── Hooks públicos ──────────────────────────────────────────────────────── */

/**
 * Lista todas las campañas detectadas en el vault. Backend agrupa por
 * `origin.source_id`. Empty state (vault vacío o sin sesiones de ad/post)
 * → `[]`, el caller muestra "Sin campañas para mostrar".
 */
export function useAdsCampaigns() {
  return useQuery<AdsCampaign[]>({
    queryKey: adsCampaignKeys.list(),
    queryFn: async () => {
      const raw = await apiClient.get<unknown>("/api/chats/ads/campaigns");
      const parsed = backendAdsCampaignsResponseSchema.parse(raw);
      return parsed.campaigns.map(mapBackendCampaign);
    },
    staleTime: 30_000,
  });
}

/**
 * Conversaciones atribuidas a una campaña específica. El backend filtra
 * por `origin.source_id == campaignId`. Si la campaña no existe →
 * `conversations: []`.
 */
export function useAttributedConversations(campaignId: string) {
  return useQuery<AttributedConversation[]>({
    queryKey: adsCampaignKeys.attributed(campaignId),
    queryFn: async () => {
      const raw = await apiClient.get<unknown>(
        `/api/chats/ads/campaigns/${encodeURIComponent(campaignId)}/conversations`,
      );
      const parsed =
        backendAttributedConversationsResponseSchema.parse(raw);
      return parsed.conversations.map(mapBackendConversation);
    },
    staleTime: 30_000,
    enabled: Boolean(campaignId),
  });
}

/**
 * Serie diaria (14 días) — SIGUE SIENDO MOCK. El backend aún no expone
 * un rollup temporal de las sesiones; cuando se agregue, este hook pasa
 * a un fetch + Zod parse análogo a los anteriores.
 */
export function useDailySeries(campaignId: string) {
  return useQuery<AdsDailyPoint[]>({
    queryKey: adsCampaignKeys.daily(campaignId),
    queryFn: async () => DAILY_SERIES,
    staleTime: Infinity,
    enabled: Boolean(campaignId),
  });
}
