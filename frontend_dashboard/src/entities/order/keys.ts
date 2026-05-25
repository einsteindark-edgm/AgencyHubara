export const orderKeys = {
  all: ["order"] as const,
  list: () => [...orderKeys.all, "list"] as const,
  detail: (id: string) => [...orderKeys.all, "detail", id] as const,
  // Premortem F2+K1: pedidos en vault local (failed registrations + stub)
  // que NO están en Medusa. El operador los reconcilia manualmente.
  vault: () => [...orderKeys.all, "vault"] as const,
} as const;
