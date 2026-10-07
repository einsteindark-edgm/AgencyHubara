import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { frequencySchema } from "./contracts";
import { frequencyKeys } from "./keys";
import type { RemarketingFrequency } from "./model";

/** Solo el cast propio (P-23): `/api/agents/*` → contrato de chats. A
 * diferencia del bot nuevo (solo por comando), este ajuste SE EDITA desde el
 * dashboard (operador, 2026-10-07): es solo una cantidad, dentro del techo de
 * Terraform, y aplica desde ya. */
const PATH = "/api/agents/remarketing/frequency";

async function fetchFrequency(signal?: AbortSignal): Promise<RemarketingFrequency> {
  return frequencySchema.parse(await apiClient.get<unknown>(PATH, { signal }));
}

export function useRemarketingFrequency() {
  return useQuery({
    queryKey: frequencyKeys.current(),
    queryFn: ({ signal }) => fetchFrequency(signal),
    staleTime: 30_000,
    // Red de seguridad: otro operador pudo haberla cambiado.
    refetchInterval: 60_000,
  });
}

/** Guarda la cantidad. El backend responde con el estado nuevo: se escribe
 * en la cache por el key factory (sin esperar otra lectura). */
export function useSetRemarketingFrequency() {
  const qc = useQueryClient();
  return useMutation<RemarketingFrequency, Error, number>({
    mutationFn: async (maxTouches) =>
      frequencySchema.parse(await apiClient.put<unknown>(PATH, { max_touches: maxTouches })),
    onSuccess: (next) => qc.setQueryData(frequencyKeys.current(), next),
  });
}
