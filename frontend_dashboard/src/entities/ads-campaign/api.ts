import { useQuery } from "@tanstack/react-query";

import { adsCampaignKeys } from "./keys";
import {
  ATTRIBUTED_CONVERSATIONS,
  CAMPAIGNS,
  DAILY_SERIES,
  type AdsCampaign,
  type AdsDailyPoint,
  type AttributedConversation,
} from "./model";

/**
 * Lista todas las campañas (mock). Cuando el plugin tenga backend, este hook
 * pasa a un `apiClient.get` + Zod parse en el boundary; la firma del hook
 * (devuelve `AdsCampaign[]`) no cambia.
 */
export function useAdsCampaigns() {
  return useQuery<AdsCampaign[]>({
    queryKey: adsCampaignKeys.list(),
    queryFn: async () => CAMPAIGNS,
    staleTime: Infinity,
  });
}

/**
 * Conversaciones atribuidas a una campaña específica. Hoy el mock devuelve
 * siempre el mismo sample (independiente del id) porque la fixture original
 * del prototipo solo cubre una campaña; cuando llegue el backend Meta sync,
 * el filtrado por `campaignId` se hace server-side.
 */
export function useAttributedConversations(campaignId: string) {
  return useQuery<AttributedConversation[]>({
    queryKey: adsCampaignKeys.attributed(campaignId),
    queryFn: async () => ATTRIBUTED_CONVERSATIONS,
    staleTime: Infinity,
    enabled: Boolean(campaignId),
  });
}

/**
 * Serie diaria (14 días) de conversaciones por estado final para una campaña.
 * Mock análogo a `useAttributedConversations`.
 */
export function useDailySeries(campaignId: string) {
  return useQuery<AdsDailyPoint[]>({
    queryKey: adsCampaignKeys.daily(campaignId),
    queryFn: async () => DAILY_SERIES,
    staleTime: Infinity,
    enabled: Boolean(campaignId),
  });
}
