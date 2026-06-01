import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { agentKeys } from "./keys";
import { agentListDtoSchema } from "./contracts";
import type { Agent } from "./model";

const _DEFAULTS: Record<string, { icon: string; color: string }> = {
  sales:       { icon: "bolt",    color: "blue"   },
  remarketing: { icon: "refresh", color: "orange" },
};

export function useAgents() {
  return useQuery({
    queryKey: agentKeys.list(),
    queryFn: async (): Promise<Agent[]> => {
      const raw = await apiClient.get<unknown>("/api/agents_admin", {
        headers: { "X-Internal-Dashboard": "1" },
      });
      const dtos = agentListDtoSchema.parse(raw);
      return dtos.map(dto => ({
        ...dto,
        model: "deepseek-chat",
        icon: (_DEFAULTS[dto.worker_name]?.icon ?? "bot") as Agent["icon"],
        color: (_DEFAULTS[dto.worker_name]?.color ?? "blue") as Agent["color"],
        status: "online" as const,
        calls: null,
        csat: null,
        category: dto.worker_name.charAt(0).toUpperCase() + dto.worker_name.slice(1),
        capabilities: [],
      }));
    },
  });
}
