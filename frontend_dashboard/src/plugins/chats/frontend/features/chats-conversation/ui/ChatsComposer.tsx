/**
 * Composer del chat con dos modos cableados a la API real:
 *
 *  1) `active_agent_route !== "humano"` → banner "Bot gestionando" + botón
 *     "Intervenir" que llama `POST /sessions/{id}/intervene`.
 *  2) `active_agent_route === "humano"` → textarea para mandar mensajes
 *     (`POST /sessions/{id}/messages`) y botón "Devolver al bot" que abre
 *     un selector (Sales / Remarketing) y llama `POST /sessions/{id}/return-to-bot`.
 *
 * El estado del modo NO es local: viene de `useSession(chatId).active_agent_route`.
 * Así, si el bot escala (`escalate_to_human`), el composer se cambia solo a
 * intervenido sin que el operador haga nada. Y al devolver al bot, se cambia
 * solo de vuelta gracias a `invalidateQueries`.
 */

import { useRef, useState } from "react";
import { Icon } from "@/shared/ui";
import { IS_MOBILE } from "@/shared/lib";
import { useSession } from "@plugins/chats/frontend/entities/session";
import {
  useInterveneMutation,
  useReturnToBotMutation,
  useSendHumanMessageMutation,
  type TargetRoute,
} from "@plugins/chats/frontend/entities/handoff";
import { useOutbox } from "../model/useOutbox";
import { ConfirmPaymentAction } from "./ConfirmPaymentAction";
import { ScheduleDeliveryAction } from "./ScheduleDeliveryAction";

/** Solo JPEG/PNG (lo que WhatsApp renderiza como `type=image`). */
const ACCEPTED_IMAGE_TYPES = "image/jpeg,image/png";

interface Props {
  chatId: string | null;
}

export function ChatsComposer({ chatId }: Props) {
  const { data: session } = useSession(chatId);
  const isHumano = session?.active_agent_route === "humano";

  if (isHumano) {
    return (
      <InterveneActiveComposer
        chatId={chatId}
        pendingPaymentOrderId={session?.pending_payment_order_id ?? null}
      />
    );
  }
  return (
    <BotManagingComposer
      chatId={chatId}
      routeLabel={session?.active_agent_route ?? "ventas"}
    />
  );
}

// ─── Modo: bot gestionando ─────────────────────────────────────────────────

interface BotManagingProps {
  chatId: string | null;
  routeLabel: string;
}

function BotManagingComposer({ chatId, routeLabel }: BotManagingProps) {
  const intervene = useInterveneMutation(chatId);

  return (
    <div className="composer composer-bot">
      <div className="bot-state">
        <span className="bot-pulse" />
        <div className="bs-body">
          <div className="bs-t">
            Bot <b>{routeLabel}</b> está gestionando esta conversación
          </div>
          <div className="bs-s">
            Si necesitas tomar el control, intervenir pausa al bot y te asigna
            la conversación.
          </div>
          {intervene.isError && (
            <div className="bs-err" role="alert">
              No se pudo intervenir: {intervene.error?.message}
            </div>
          )}
        </div>
        <button
          className="interv-btn"
          onClick={() => intervene.mutate({})}
          disabled={!chatId || intervene.isPending}
        >
          <Icon.user />
          <span>{intervene.isPending ? "Tomando…" : "Intervenir"}</span>
        </button>
      </div>
    </div>
  );
}

// ─── Modo: humano interviniendo ────────────────────────────────────────────

interface InterveneActiveProps {
  chatId: string | null;
  /** Pedido esperando confirmación de pago (id backend), o null si no hay. */
  pendingPaymentOrderId: string | null;
}

function InterveneActiveComposer({
  chatId,
  pendingPaymentOrderId,
}: InterveneActiveProps) {
  const [text, setText] = useState("");
  const [showReturnPicker, setShowReturnPicker] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  // PM-007: un solo popover de acción de pedido abierto a la vez — con los
  // dos abiertos se superponían (mismo anclaje right:0) y el operador podía
  // disparar schedule desde ambos en paralelo.
  const [openAction, setOpenAction] = useState<"schedule" | "confirm" | null>(
    null,
  );

  const sendMessage = useSendHumanMessageMutation(chatId);
  const outbox = useOutbox(chatId);

  const onSend = () => {
    const value = text.trim();
    // Enviar la FOTO no bloquea el texto: mandar texto es independiente. Si el
    // operador escribió algo, lo mandamos como mensaje de texto normal.
    if (!value || !chatId || sendMessage.isPending) return;
    sendMessage.mutate({ text: value }, { onSuccess: () => setText("") });
  };

  // Picker nativo → encola cada foto en el outbox (usa el texto actual como
  // caption y lo limpia). El composer nunca se bloquea; se pueden encolar más.
  const onPickFiles = (list: FileList | null) => {
    if (!chatId || !list || list.length === 0) return;
    const files = Array.from(list);
    void outbox.enqueue(files, text.trim());
    setText("");
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (cameraInputRef.current) cameraInputRef.current.value = "";
  };

  return (
    <div className="composer">
      <div className="composer-tools">
        <span className="interv-on">
          <Icon.user />
          Intervenido por ti · Bot pausado
        </span>
        <span className="right">
          {pendingPaymentOrderId && (
            <>
              <ScheduleDeliveryAction
                orderId={pendingPaymentOrderId}
                open={openAction === "schedule"}
                onOpenChange={(v) => setOpenAction(v ? "schedule" : null)}
              />
              <ConfirmPaymentAction
                orderId={pendingPaymentOrderId}
                open={openAction === "confirm"}
                onOpenChange={(v) => setOpenAction(v ? "confirm" : null)}
              />
            </>
          )}
          <button
            className="interv-off"
            onClick={() => setShowReturnPicker(true)}
            disabled={!chatId}
          >
            Devolver al bot
          </button>
          <kbd>⌘↩</kbd>
        </span>
      </div>

      {outbox.items.length > 0 && (
        <OutboxStrip items={outbox.items} onRetry={outbox.retry} onRemove={outbox.remove} />
      )}

      <div className="composer-row">
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_IMAGE_TYPES}
          multiple
          hidden
          data-testid="chat-file-input"
          onChange={(e) => onPickFiles(e.target.files)}
        />
        {/* `capture=environment` abre la cámara trasera en Android; solo móvil. */}
        {IS_MOBILE && (
          <input
            ref={cameraInputRef}
            type="file"
            accept={ACCEPTED_IMAGE_TYPES}
            capture="environment"
            hidden
            data-testid="chat-camera-input"
            onChange={(e) => onPickFiles(e.target.files)}
          />
        )}
        <button
          type="button"
          className="attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={!chatId}
          title="Adjuntar foto"
          aria-label="Adjuntar foto"
        >
          <Icon.attach />
        </button>
        {IS_MOBILE && (
          <button
            type="button"
            className="camera-btn"
            onClick={() => cameraInputRef.current?.click()}
            disabled={!chatId}
            title="Tomar foto"
            aria-label="Tomar foto"
          >
            <Icon.img />
          </button>
        )}
        <textarea
          placeholder="Escribe un mensaje al cliente…"
          rows={1}
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          disabled={sendMessage.isPending}
        />
        <button
          className="send-btn"
          onClick={onSend}
          disabled={!text.trim() || sendMessage.isPending}
          title={sendMessage.isPending ? "Enviando…" : "Enviar (⌘↩)"}
        >
          <Icon.send />
        </button>
      </div>
      {sendMessage.isError && (
        <div className="composer-err" role="alert">
          No se pudo enviar: {sendMessage.error?.message}
        </div>
      )}
      {showReturnPicker && (
        <ReturnToBotPicker
          chatId={chatId}
          onClose={() => setShowReturnPicker(false)}
        />
      )}
    </div>
  );
}

// ─── Tira de fotos en vuelo (optimista) ────────────────────────────────────

interface OutboxStripProps {
  items: ReturnType<typeof useOutbox>["items"];
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
}

function OutboxStrip({ items, onRetry, onRemove }: OutboxStripProps) {
  return (
    <div className="outbox-strip" aria-label="Fotos en envío">
      {items.map((it) => (
        <div key={it.id} className={`outbox-item is-${it.status}`}>
          {it.previewUrl && <img src={it.previewUrl} alt="Foto en envío" />}
          {it.status === "uploading" && (
            <div className="outbox-progress" role="progressbar">
              <span style={{ width: `${Math.round(it.progress * 100)}%` }} />
            </div>
          )}
          {(it.status === "compressing" || it.status === "sending") && (
            <span className="outbox-spinner" aria-label="Enviando" />
          )}
          {it.status === "failed" && (
            <div className="outbox-failed">
              <button
                type="button"
                className="outbox-retry"
                onClick={() => onRetry(it.id)}
                title={it.error || "Reintentar"}
              >
                Reintentar
              </button>
              <button
                type="button"
                className="outbox-discard"
                onClick={() => onRemove(it.id)}
                aria-label="Descartar"
                title="Descartar"
              >
                ✕
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Modal de selección Sales / Remarketing ────────────────────────────────

interface ReturnPickerProps {
  chatId: string | null;
  onClose: () => void;
}

function ReturnToBotPicker({ chatId, onClose }: ReturnPickerProps) {
  const [target, setTarget] = useState<TargetRoute>("ventas");
  const [motivo, setMotivo] = useState("");
  const returnToBot = useReturnToBotMutation(chatId);

  const submit = () => {
    if (!chatId || returnToBot.isPending) return;
    if (target === "remarketing" && !motivo.trim()) return;
    returnToBot.mutate(
      {
        target_route: target,
        motivo: motivo.trim() || undefined,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <div
      className="return-picker-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="return-picker"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="rp-header">
          <b>Devolver al bot</b>
          <button className="tb-btn" onClick={onClose} title="Cerrar">
            ✕
          </button>
        </div>
        <p className="rp-sub">
          ¿A qué bot quieres devolver la conversación?
        </p>
        <label className="rp-opt">
          <input
            type="radio"
            name="target_route"
            value="ventas"
            checked={target === "ventas"}
            onChange={() => setTarget("ventas")}
          />
          <span>
            <b>Sales</b> — el bot espera al cliente; arranca al próximo mensaje.
          </span>
        </label>
        <label className="rp-opt">
          <input
            type="radio"
            name="target_route"
            value="remarketing"
            checked={target === "remarketing"}
            onChange={() => setTarget("remarketing")}
          />
          <span>
            <b>Remarketing</b> — el bot arranca AHORA y manda un gancho al cliente.
          </span>
        </label>
        {target === "remarketing" && (
          <textarea
            className="rp-motivo"
            placeholder="Motivo del gancho (qué quedó pendiente). El bot lo usa para personalizar el mensaje."
            rows={3}
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
          />
        )}
        {target === "ventas" && (
          <textarea
            className="rp-motivo"
            placeholder="Motivo (opcional) para que quede en el historial."
            rows={2}
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
          />
        )}
        {returnToBot.isError && (
          <div className="composer-err" role="alert">
            Error: {returnToBot.error?.message}
          </div>
        )}
        <div className="rp-actions">
          <button className="tb-btn" onClick={onClose}>Cancelar</button>
          <button
            className="interv-btn"
            onClick={submit}
            disabled={
              returnToBot.isPending ||
              (target === "remarketing" && !motivo.trim())
            }
          >
            {returnToBot.isPending ? "Devolviendo…" : "Confirmar"}
          </button>
        </div>
      </div>
    </div>
  );
}
