/**
 * Composición raíz de providers. Cualquier provider nuevo (Theme, Auth, etc.)
 * se enchufa acá — `main.tsx` queda inalterado.
 *
 * `ErrorBoundary scope="app"` es la última red (F2.1): si algo revienta fuera
 * del boundary por-sección del Dashboard, el operador ve un fallback con
 * "Reintentar" en vez de pantalla blanca.
 */

import type { ReactNode } from "react";
import { ErrorBoundary } from "@/shared/ui";
import { QueryProvider } from "./QueryProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary scope="app">
      <QueryProvider>{children}</QueryProvider>
    </ErrorBoundary>
  );
}
