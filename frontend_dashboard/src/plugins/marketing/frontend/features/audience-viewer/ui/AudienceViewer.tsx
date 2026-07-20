/**
 * Visor de audiencia — modal overlay PROPIO (regla #6: cero diálogos JS
 * nativos) con layout dos paneles estilo Chats: izquierda la lista de la
 * audiencia (seleccionable) + CURADURÍA manual, centro la conversación en
 * burbujas read-only.
 *
 * Curaduría (solo campañas editables draft/scheduled):
 *  - "×" en cada fila → PUT `excluded_session_ids` + la sesión (o la saca de
 *    `extra_session_ids` si era un agregado manual).
 *  - "Quitados por vos" → "Restaurar" la saca de `excluded_session_ids`.
 *  - Input "Agregar número" → PUT `extra_session_ids` + `wa_<normalizado>`;
 *    el 422 del backend (sin conversación previa) se superficie bajo el input.
 * Las listas viajan como REPLACE completo. La audiencia se refresca por la
 * cadena existente: PUT → invalidate campañas → updated_at_ms nuevo → key
 * nueva de la query de audiencia (ver entities/audience/keys.ts).
 *
 * Convención visual de Chats: mensajes del cliente (`role: "user"`) a la
 * IZQUIERDA con `--color-bubble-out` (gris) y mensajes del negocio
 * (`assistant`) a la DERECHA con `--color-bubble-in` (azul) — el mismo
 * mapeo token↔lado que `.b-in`/`.b-out` en index.css.
 */

import { useEffect, useState } from "react";

import { Icon } from "@/shared/ui";

import {
  phoneToSessionId,
  useAudienceConversation,
} from "@plugins/marketing/frontend/entities/audience";
import type {
  AudienceRecipient,
  ConversationMessage,
  SkippedContact,
} from "@plugins/marketing/frontend/entities/audience";
import {
  isCampaignEditable,
  useUpdateCampaign,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import { apiErrorDetail, fmtN } from "@plugins/marketing/frontend/lib/format";

import { RecipientRow } from "./RecipientRow";

interface Props {
  /** Campaña dueña de la audiencia — id + listas de curaduría + status. */
  campaign: Campaign;
  /** La misma audiencia del tab — panel izquierdo del visor. */
  recipients: AudienceRecipient[];
  /** Skipped con razón "quitado_por_operador" — sección restaurable. */
  removed: SkippedContact[];
  /** Total confirmado del endpoint (footer del panel izquierdo). */
  total: number;
  /** Sesión activa (estado del inspector; `null` = visor cerrado, no montado). */
  sessionId: string;
  onSelectSession: (sessionId: string) => void;
  onClose: () => void;
}

export function AudienceViewer({
  campaign,
  recipients,
  removed,
  total,
  sessionId,
  onSelectSession,
  onClose,
}: Props) {
  const { data, isPending, error } = useAudienceConversation(sessionId);
  const editable = isCampaignEditable(campaign.status);

  // Dos mutations independientes del MISMO PUT: los errores de quitar /
  // restaurar no se mezclan con el 422 del alta de número (regla #3: el
  // error se DERIVA de cada mutation, sin useState duplicado).
  const curate = useUpdateCampaign(campaign.id);
  const add = useUpdateCampaign(campaign.id);
  const [phone, setPhone] = useState("");

  // Escape cierra — listener global mientras el modal está montado.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const active = recipients.find((r) => r.sessionId === sessionId);

  const removeRecipient = (r: AudienceRecipient) => {
    if (r.segment === "manual") {
      // Un agregado manual se quita de extra_session_ids (no de excluded).
      curate.mutate({
        extraSessionIds: campaign.extraSessionIds.filter(
          (id) => id !== r.sessionId,
        ),
      });
    } else {
      curate.mutate({
        excludedSessionIds: [...campaign.excludedSessionIds, r.sessionId],
      });
    }
  };

  const restoreRemoved = (removedSessionId: string) => {
    curate.mutate({
      excludedSessionIds: campaign.excludedSessionIds.filter(
        (id) => id !== removedSessionId,
      ),
    });
  };

  const addPhone = () => {
    const trimmed = phone.trim();
    if (!trimmed) return;
    add.mutate(
      { extraSessionIds: [...campaign.extraSessionIds, phoneToSessionId(trimmed)] },
      { onSuccess: () => setPhone("") },
    );
  };

  return (
    <div
      data-testid="audience-viewer-backdrop"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Conversación con ${active?.phone ?? sessionId}`}
        onClick={(e) => e.stopPropagation()}
        className="flex h-[72vh] w-[760px] max-w-full overflow-hidden rounded-xl border border-line bg-canvas shadow-2xl"
      >
        {/* Panel izquierdo: la audiencia + curaduría */}
        <aside className="flex w-[264px] shrink-0 flex-col border-r border-line bg-sidebar">
          <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-2">
            {recipients.map((r) => (
              <div key={r.sessionId} className="flex items-center gap-1">
                <RecipientRow
                  recipient={r}
                  active={r.sessionId === sessionId}
                  onClick={() => onSelectSession(r.sessionId)}
                />
                {editable ? (
                  <button
                    type="button"
                    aria-label={`Quitar ${r.phone}`}
                    onClick={() => removeRecipient(r)}
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-fg-faint hover:bg-danger-soft hover:text-danger"
                  >
                    <Icon.x />
                  </button>
                ) : null}
              </div>
            ))}

            {removed.length > 0 ? (
              <section className="mt-2 opacity-70">
                <h3 className="px-2.5 text-[10.5px] font-semibold uppercase tracking-wide text-fg-muted">
                  Quitados por vos ({fmtN(removed.length)})
                </h3>
                <ul className="mt-1 flex flex-col gap-0.5">
                  {removed.map((s) => (
                    <li
                      key={s.sessionId}
                      className="flex items-center gap-2 rounded-md px-2.5 py-1"
                    >
                      <span className="min-w-0 flex-1 truncate text-[11px] tabular-nums text-fg-faint">
                        {s.phone}
                      </span>
                      {editable ? (
                        <button
                          type="button"
                          onClick={() => restoreRemoved(s.sessionId)}
                          className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10.5px] font-semibold text-fg-muted hover:bg-white/[0.05] hover:text-fg"
                        >
                          Restaurar
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {curate.error ? (
              <p className="px-2.5 py-1 text-[10.5px] leading-snug text-danger">
                {apiErrorDetail(curate.error)}
              </p>
            ) : null}

            {editable ? (
              <div className="mt-2 flex flex-col gap-1 px-1">
                <div className="flex items-center gap-1">
                  <input
                    type="tel"
                    aria-label="Agregar número"
                    placeholder="+57 300 123 4567"
                    value={phone}
                    disabled={add.isPending}
                    onChange={(e) => setPhone(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") addPhone();
                    }}
                    className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-2 py-1 text-[11.5px] tabular-nums text-fg outline-none placeholder:text-fg-faint focus:border-accent"
                  />
                  <button
                    type="button"
                    disabled={add.isPending || phone.trim() === ""}
                    onClick={addPhone}
                    className="shrink-0 rounded-md border border-line px-2 py-1 text-[11px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
                  >
                    Agregar
                  </button>
                </div>
                {add.error ? (
                  <p className="px-1 text-[10.5px] leading-snug text-danger">
                    {apiErrorDetail(add.error)}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>

          <footer className="flex shrink-0 flex-col gap-1.5 border-t border-line p-2.5">
            <p className="text-[11.5px] font-semibold tabular-nums text-fg">
              {fmtN(total)} destinatarios confirmados
            </p>
            {editable ? (
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-accent px-3 py-1.5 text-[11.5px] font-semibold text-white hover:opacity-90"
              >
                Confirmar audiencia
              </button>
            ) : null}
          </footer>
        </aside>

        {/* Centro: conversación read-only */}
        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
            <span className="text-[12.5px] font-semibold tabular-nums text-fg">
              {active?.phone ?? sessionId}
            </span>
            <span className="truncate text-[12px] text-fg-muted">
              {active?.customerName ?? "—"}
            </span>
            <button
              type="button"
              aria-label="Cerrar"
              onClick={onClose}
              className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-fg-muted hover:bg-white/[0.06] hover:text-fg"
            >
              <Icon.x />
            </button>
          </header>

          <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-3">
            {isPending ? (
              <p className="m-auto text-[11.5px] text-fg-faint">
                Cargando conversación…
              </p>
            ) : error ? (
              <p className="m-auto text-[11.5px] text-danger">
                {apiErrorDetail(error)}
              </p>
            ) : !data || data.messages.length === 0 ? (
              <p className="m-auto text-[11.5px] text-fg-faint">
                Sin historial de conversación
              </p>
            ) : (
              data.messages.map((m, i) => <MessageBubble key={i} message={m} />)
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/* ── Burbuja read-only ──────────────────────────────────────────────────── */

function MessageBubble({ message }: { message: ConversationMessage }) {
  const out = message.role === "assistant";
  return (
    <div
      className={
        "max-w-[78%] rounded-[14px] px-3 py-2 text-[12.5px] leading-snug " +
        (out
          ? "self-end rounded-br-[5px] bg-bubble-in text-white"
          : "self-start rounded-bl-[5px] bg-bubble-out text-fg")
      }
    >
      {message.kind === "template" ? (
        <>
          <span className="mb-1 block text-[9.5px] font-bold uppercase tracking-wide text-white/70">
            Template
          </span>
          <p className="whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed">
            {message.content}
          </p>
        </>
      ) : (
        <p className="whitespace-pre-wrap">{message.content}</p>
      )}
      {message.timestamp ? (
        <p
          className={
            "mt-1 text-[10px] tabular-nums " +
            (out ? "text-white/60" : "text-fg-faint")
          }
        >
          {message.timestamp}
        </p>
      ) : null}
    </div>
  );
}
