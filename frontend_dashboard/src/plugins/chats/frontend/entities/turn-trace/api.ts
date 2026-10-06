import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { turnThreadSchema } from "./contracts";
import { turnTraceKeys } from "./keys";
import type { TurnThread } from "./model";

async function fetchThread(sid: string, turnKey: string, signal?: AbortSignal): Promise<TurnThread> {
  const query = new URLSearchParams({ turn_key: turnKey }).toString();
  const url = `/api/chats/sessions/${encodeURIComponent(sid)}/turns/trace?${query}`;
  return turnThreadSchema.parse(await apiClient.get<unknown>(url, { signal }));
}

/** El hilo de un turno: la traza no cambia una vez escrita. */
export function useTurnThread(sid: string | null, turnKey: string | null) {
  return useQuery({
    queryKey: turnTraceKeys.thread(sid ?? "", turnKey ?? ""),
    queryFn: ({ signal }) => fetchThread(sid as string, turnKey as string, signal),
    enabled: Boolean(sid && turnKey),
    staleTime: Infinity,
    retry: false,
  });
}
