/**
 * Sección Reseñas — lo que opinan los clientes después de recibir su pedido.
 *
 * Una sola pantalla de ancho completo (sin sidebar ni inspector): arriba el
 * resumen (promedio, reparto, invitaciones, cupones y si el envío está
 * prendido), abajo las bandejas para moderar. Los Pages no reciben props
 * (PluginHost); cada feature trae sus datos de la entity local, que solo
 * llama a `/api/reviews/*`. Sin lógica acá: solo composición.
 */

import { ReviewModeration } from "@plugins/reviews/frontend/features/review-moderation";
import { ReviewSummary } from "@plugins/reviews/frontend/features/review-summary";

export default function Page() {
  return (
    <section
      aria-label="Reseñas"
      className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-win-bg text-fg"
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-line bg-titlebar px-4 py-2.5">
        <h1 className="m-0 text-[13px] font-semibold">Reseñas</h1>
        <p className="m-0 text-xs text-fg-muted">
          Lo que opinan los clientes después de recibir su pedido.
        </p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <ReviewSummary />
        <div className="border-t border-line">
          <ReviewModeration />
        </div>
      </div>
    </section>
  );
}
