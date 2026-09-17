/** Query keys de la entity `order-intake` (local a chats). */
export const orderIntakeKeys = {
  all: ["chats", "order-intake"] as const,
  suggestion: (sessionId: string) =>
    [...orderIntakeKeys.all, "suggestion", sessionId] as const,
};
