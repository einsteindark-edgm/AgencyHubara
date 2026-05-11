/**
 * Composición raíz de providers. Cualquier provider nuevo (Theme, Auth,
 * ErrorBoundary global, etc.) se enchufa acá — `main.tsx` queda inalterado.
 */

import type { ReactNode } from "react";
import { QueryProvider } from "./QueryProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return <QueryProvider>{children}</QueryProvider>;
}
