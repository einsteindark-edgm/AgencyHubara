/**
 * Una reseña con lo que el equipo necesita para decidir: estrellas, pedido,
 * productos, comentario, fotos, quién y cuándo (hora de Colombia), el cupón
 * que recibió y los avisos. Acciones según la bandeja:
 *  - Por revisar: Publicar, Ocultar, Responder
 *  - Publicadas:  Ocultar, Responder
 *  - Ocultas:     Publicar, Responder
 * Sin permiso del cliente, «Publicar» queda apagado (solo cuenta en el
 * promedio). Los errores salen de cada mutation (regla #3), no de un estado
 * copiado.
 */

import { useState } from "react";

import { apiFileUrl } from "@/shared/api";
import { Icon } from "@/shared/ui";

import {
  Stars,
  TONE_CLASS,
  couponChipLabel,
  formatReviewDate,
  reviewErrorDetail,
  useApproveReview,
  useHideReview,
  type Review,
} from "@plugins/reviews/frontend/entities/review";

import { ReplyEditor } from "./ReplyEditor";

const BTN_PRIMARY =
  "rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const BTN_SECONDARY =
  "rounded-md border border-line-strong px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-line disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export function ReviewCard({ review }: { review: Review }) {
  const approve = useApproveReview(review.reviewId);
  const hide = useHideReview(review.reviewId);
  const [replying, setReplying] = useState(false);

  const orderLabel = `Pedido ${review.displayId ?? "sin número"}`;
  const author = review.authorName?.trim() || "Cliente";
  const busy = approve.isPending || hide.isPending;
  const consentHintId = `reviews-consent-${review.reviewId}`;
  const publishError = approve.error
    ? reviewErrorDetail(approve.error)
    : review.published?.error
      ? `No se pudo publicar en la tienda: ${review.published.error}`
      : null;

  return (
    <article
      aria-label={`Reseña del pedido ${review.displayId ?? "sin número"}`}
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-line bg-sidebar p-3"
    >
      <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Stars value={review.rating} />
        <span className="text-[12.5px] font-semibold text-fg">{orderLabel}</span>
        <time
          dateTime={new Date(review.submittedAtMs).toISOString()}
          className="ml-auto text-[11.5px] tabular-nums text-fg-muted"
        >
          {/* Reloj leído en render (regla 5): el año solo aparece si no es este. */}
          {formatReviewDate(review.submittedAtMs)}
        </time>
      </header>

      {review.products.length > 0 ? (
        <p className="m-0 text-[12px] text-fg-soft">
          {review.products.map((p) => p.title).join(" · ")}
        </p>
      ) : null}

      {review.comment?.trim() ? (
        <p className="m-0 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-fg">
          {review.comment}
        </p>
      ) : (
        <p className="m-0 text-[12.5px] italic text-fg-muted">Sin comentario</p>
      )}

      {review.photoUrls.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {review.photoUrls.map((url, i) => {
            const src = apiFileUrl(url);
            return (
              <a
                key={url}
                href={src}
                target="_blank"
                rel="noreferrer"
                className="block rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <img
                  src={src}
                  alt={`Foto ${i + 1} de la reseña del ${orderLabel.toLowerCase()}`}
                  loading="lazy"
                  className="h-16 w-16 rounded-md border border-line bg-canvas object-cover"
                />
              </a>
            );
          })}
        </div>
      ) : null}

      <p className="m-0 text-[12px] text-fg-muted">
        {review.authorCity ? `${author} · ${review.authorCity}` : author}
      </p>

      {review.coupon || review.teamAlerted || !review.publishConsent ? (
        <div className="flex flex-wrap gap-1.5">
          {review.coupon ? (
            <span
              title="Cupón que recibió el cliente por calificar"
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${TONE_CLASS.ok}`}
            >
              <Icon.tag />
              {couponChipLabel(review.coupon)}
            </span>
          ) : null}
          {review.teamAlerted ? (
            <span className={`rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${TONE_CLASS.warn}`}>
              Se avisó al equipo
            </span>
          ) : null}
          {!review.publishConsent ? (
            <span
              id={consentHintId}
              className={`rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${TONE_CLASS.neutral}`}
            >
              Sin permiso para publicar: solo cuenta en el promedio
            </span>
          ) : null}
        </div>
      ) : null}

      {publishError ? (
        <p role="alert" className={`m-0 rounded-md px-2.5 py-1.5 text-[12px] ${TONE_CLASS.danger}`}>
          {publishError}
        </p>
      ) : null}
      {hide.error ? (
        <p role="alert" className={`m-0 rounded-md px-2.5 py-1.5 text-[12px] ${TONE_CLASS.danger}`}>
          {reviewErrorDetail(hide.error)}
        </p>
      ) : null}

      {replying ? (
        <ReplyEditor
          reviewId={review.reviewId}
          author={author}
          initial={review.reply ?? ""}
          onClose={() => setReplying(false)}
        />
      ) : review.reply ? (
        <div className="rounded-md border border-line bg-canvas px-2.5 py-2">
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
            Respuesta pública (sale en la web)
          </p>
          <p className="m-0 mt-1 whitespace-pre-wrap break-words text-[12.5px] text-fg-soft">
            {review.reply}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        {review.status !== "approved" ? (
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={busy || !review.publishConsent}
            aria-describedby={review.publishConsent ? undefined : consentHintId}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? "Publicando…" : "Publicar"}
          </button>
        ) : null}
        {review.status !== "hidden" ? (
          <button
            type="button"
            className={BTN_SECONDARY}
            disabled={busy}
            onClick={() => hide.mutate()}
          >
            {hide.isPending ? "Ocultando…" : "Ocultar"}
          </button>
        ) : null}
        {!replying ? (
          <button type="button" className={BTN_SECONDARY} onClick={() => setReplying(true)}>
            {review.reply ? "Cambiar respuesta" : "Responder"}
          </button>
        ) : null}
      </div>
    </article>
  );
}
