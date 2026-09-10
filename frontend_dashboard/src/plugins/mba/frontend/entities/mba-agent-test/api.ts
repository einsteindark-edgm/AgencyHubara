/**
 * Hook de `mba-agent-test` (D2.4): un turno del simulador de Meta. Es una
 * mutation (cada envío es una llamada nueva); el hilo de la consola es UI
 * state del feature (el simulador no tiene lectura de historial).
 */
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { mbaAgentTestOutcomeSchema, type MbaAgentTestOutcome } from "./contracts";

export interface AgentTestTurn {
  message: string;
  conversationId?: string | null;
}

export function useMbaAgentTest(agentId: string) {
  return useMutation<MbaAgentTestOutcome, Error, AgentTestTurn>({
    mutationFn: async ({ message, conversationId }) =>
      mbaAgentTestOutcomeSchema.parse(
        await apiClient.post<unknown>(
          `/api/mba/agents/${encodeURIComponent(agentId)}/test`,
          conversationId ? { message, conversation_id: conversationId } : { message },
        ),
      ),
  });
}
