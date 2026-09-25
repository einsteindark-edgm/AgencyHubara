/** TanStack Query key factory del hilo de un turno. */
export const turnTraceKeys = {
  all: ["chats", "turn-trace"] as const,
  thread: (sid: string, turnKey: string) => [...turnTraceKeys.all, sid, turnKey] as const,
} as const;
