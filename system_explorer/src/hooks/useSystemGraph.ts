import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../api/client";
import { SystemGraphSchema, type SystemGraph } from "../api/schemas";

// TanStack Query: carga el grafo via fetch + Zod parse.
// `staleTime` config global en main.tsx (30s). Reintenta 2x en errores
// transitorios (network blip). En error final, el caller renderiza el detalle.
export function useSystemGraph() {
  return useQuery<SystemGraph>({
    queryKey: ["system-graph"],
    queryFn: async () => {
      const raw = await apiGet("/api/system-map/graph");
      // Zod parse al boundary — si el backend cambia el shape sin migrar el
      // schema, esto tira un ZodError descriptivo (no errors silenciosos).
      return SystemGraphSchema.parse(raw);
    },
  });
}
