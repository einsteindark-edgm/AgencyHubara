export const orderKeys = {
  all: ["order"] as const,
  list: () => [...orderKeys.all, "list"] as const,
  detail: (id: string) => [...orderKeys.all, "detail", id] as const,
  // Premortem F2+K1: pedidos en vault local (failed registrations + stub)
  // que NO están en Medusa. El operador los reconcilia manualmente.
  vault: () => [...orderKeys.all, "vault"] as const,
  // Customer scoring: panel "Historial cliente" del inspector. Por order_id
  // (display_id) — el backend resuelve internamente el session_id del cliente.
  customerScore: (orderId: string) =>
    [...orderKeys.all, "customer-score", orderId] as const,
} as const;
