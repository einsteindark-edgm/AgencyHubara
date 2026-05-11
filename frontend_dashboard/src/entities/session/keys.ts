/**
 * Query key factory. Centraliza todas las claves de cache para sesiones —
 * ningún consumer debe hardcodear `["sessions", "detail", id]`.
 *
 * Patrón estándar TanStack Query (https://tkdodo.eu/blog/effective-react-query-keys).
 * Permite invalidación selectiva:
 *   - `invalidateQueries({ queryKey: sessionKeys.all })` → todo
 *   - `invalidateQueries({ queryKey: sessionKeys.detail(id) })` → un solo detalle
 */

export const sessionKeys = {
  all: ["sessions"] as const,
  list: () => [...sessionKeys.all, "list"] as const,
  detail: (id: string) => [...sessionKeys.all, "detail", id] as const,
} as const;
