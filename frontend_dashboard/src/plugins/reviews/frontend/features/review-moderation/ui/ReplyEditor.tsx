/**
 * Cuadro para escribir (o cambiar) la respuesta de la tienda a una reseña.
 * El borrador es estado local del cuadro; el error sale de la mutation. Al
 * guardar se cierra; «Cancelar» lo cierra sin guardar.
 */

import { useState } from "react";

import {
  REPLY_MAX_CHARS,
  TONE_CLASS,
  reviewErrorDetail,
  useReplyReview,
} from "@plugins/reviews/frontend/entities/review";

export function ReplyEditor({
  reviewId,
  author,
  initial,
  onClose,
}: {
  reviewId: string;
  author: string;
  initial: string;
  onClose: () => void;
}) {
  const reply = useReplyReview(reviewId);
  const [draft, setDraft] = useState(initial);
  const text = draft.trim();
  const fieldId = `reviews-reply-${reviewId}`;

  return (
    <form
      className="flex flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text || reply.isPending) return;
        reply.mutate(text, { onSuccess: onClose });
      }}
    >
      <label htmlFor={fieldId} className="text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
        Respuesta pública (sale en la web)
      </label>
      <textarea
        id={fieldId}
        aria-label={`Respuesta para ${author}`}
        value={draft}
        maxLength={REPLY_MAX_CHARS}
        rows={3}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Escribe una respuesta corta y cálida."
        className="w-full resize-y rounded-md border border-line-strong bg-canvas px-2.5 py-2 text-[12.5px] text-fg placeholder:text-fg-faint focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] tabular-nums text-fg-muted">
          {`${draft.length}/${REPLY_MAX_CHARS}`}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded-md border border-line-strong px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Cancelar
        </button>
        <button
          type="submit"
          disabled={!text || reply.isPending}
          className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {reply.isPending ? "Enviando…" : "Enviar respuesta"}
        </button>
      </div>
      {reply.error ? (
        <p role="alert" className={`m-0 rounded-md px-2.5 py-1.5 text-[12px] ${TONE_CLASS.danger}`}>
          {reviewErrorDetail(reply.error)}
        </p>
      ) : null}
    </form>
  );
}
