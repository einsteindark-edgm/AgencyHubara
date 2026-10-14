/**
 * Page de reviews — los Pages NO reciben props (PluginHost, F5):
 * estado de selección vía useSelection("reviews"); datos vía la entity
 * local (Zod en el boundary) llamando SOLO /api/reviews/*.
 */
import { useSelection } from "@/shared/sdk";

export default function Page() {
  const [selectedId] = useSelection("reviews");
  return (
    <section className="flex flex-col gap-2 p-4">
      <h1 className="text-lg font-semibold">Reviews</h1>
      <p className="text-sm opacity-70">
        Plugin generado por <code>hubara create</code>. Reemplazá este Page
        por tu mini-FSD (entities → features → pages).
      </p>
      {selectedId ? <p className="text-sm">Seleccionado: {selectedId}</p> : null}
    </section>
  );
}
