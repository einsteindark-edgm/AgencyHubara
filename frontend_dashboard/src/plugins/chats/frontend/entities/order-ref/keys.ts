/** Query keys de la entity `order-ref` (local a chats — no colisiona con orders). */
export const orderRefKeys = {
  all: ["chats", "order-ref"] as const,
  detail: (orderId: string) => [...orderRefKeys.all, "detail", orderId] as const,
  bySession: (sessionId: string) =>
    [...orderRefKeys.all, "by-session", sessionId] as const,
};
