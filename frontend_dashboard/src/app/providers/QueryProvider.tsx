/**
 * QueryClientProvider con defaults pensados para un dashboard internal-tool:
 *   - `staleTime: 5s` — evita refetch en cada mount, pero no envejece de más.
 *   - `gcTime: 5min` — cache razonable para sesiones que el user navega.
 *   - `retry: 1` — un solo retry; los errores de red en LAN suelen ser permanentes.
 *   - `refetchOnWindowFocus: false` — el SSE empuja updates; no necesitamos focus refetch.
 *
 * Devtools sólo en dev (los excluye Vite en build).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            gcTime: 5 * 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      {children}
      {import.meta.env.DEV ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  );
}
