/**
 * Moderación de reseñas: tres bandejas (Por revisar / Publicadas / Ocultas)
 * con sus conteos y la lista de tarjetas de la bandeja elegida. La bandeja es
 * estado de UI local (regla #3); las reseñas viven en TanStack Query.
 *
 * Al cambiar de bandeja los conteos se conservan mientras carga
 * (`isPlaceholderData`) — la lista, en cambio, no muestra las tarjetas de la
 * bandeja anterior: dice «Cargando…».
 */

import { useState } from "react";

import {
  REVIEW_TABS,
  reviewErrorDetail,
  useReviews,
  type ReviewStatus,
} from "@plugins/reviews/frontend/entities/review";

import { ReviewCard } from "./ReviewCard";

const EMPTY_TEXT: Record<ReviewStatus, string> = {
  pending: "No hay reseñas por revisar. Cuando un cliente califique su pedido, aparece aquí.",
  approved: "Todavía no hay reseñas publicadas.",
  hidden: "No hay reseñas ocultas.",
};

export function ReviewModeration() {
  const [status, setStatus] = useState<ReviewStatus>("pending");
  const { data, error, isError, isPlaceholderData, refetch } = useReviews(status);
  const counts = data?.counts;

  return (
    <section aria-label="Reseñas para moderar" className="flex min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="Bandejas de reseñas"
        className="flex gap-0.5 overflow-x-auto border-b border-line px-3"
      >
        {REVIEW_TABS.map((tab) => {
          const selected = tab.status === status;
          return (
            <button
              key={tab.status}
              type="button"
              role="tab"
              id={`reviews-tab-${tab.status}`}
              aria-selected={selected}
              aria-controls="reviews-tab-panel"
              onClick={() => setStatus(tab.status)}
              className={
                "whitespace-nowrap border-0 border-b-2 bg-transparent px-2.5 py-[11px] text-[12.5px] font-medium leading-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent " +
                (selected ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg-soft")
              }
            >
              {counts ? `${tab.label} (${counts[tab.status]})` : tab.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id="reviews-tab-panel"
        aria-labelledby={`reviews-tab-${status}`}
        className="px-4 py-3"
      >
        {isError && !data ? (
          <div role="alert" className="flex flex-col items-start gap-2 py-6">
            <p className="text-[12.5px] font-semibold text-danger">No se pudieron cargar las reseñas.</p>
            <p className="text-[12px] text-fg-muted">{reviewErrorDetail(error)}</p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="rounded-md border border-line-strong px-2.5 py-1 text-[12px] font-semibold text-fg hover:bg-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Reintentar
            </button>
          </div>
        ) : !data || isPlaceholderData ? (
          <p className="py-6 text-[12.5px] text-fg-muted">Cargando reseñas…</p>
        ) : data.reviews.length === 0 ? (
          <p className="py-6 text-[12.5px] text-fg-muted">{EMPTY_TEXT[status]}</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {data.reviews.map((review) => (
              <ReviewCard key={review.reviewId} review={review} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
