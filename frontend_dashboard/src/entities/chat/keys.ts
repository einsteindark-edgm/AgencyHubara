export const chatKeys = {
  all: ["chat-inbox"] as const,
  list: () => [...chatKeys.all, "list"] as const,
  messages: (id: string) => [...chatKeys.all, "messages", id] as const,
  memory: (id: string) => [...chatKeys.all, "memory", id] as const,
  routingLog: (id: string) => [...chatKeys.all, "routing", id] as const,
  notes: (id: string) => [...chatKeys.all, "notes", id] as const,
  files: (id: string) => [...chatKeys.all, "files", id] as const,
} as const;
