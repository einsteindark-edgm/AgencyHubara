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

import { useState } from "react";
import { Icon } from "@/shared/ui";
import { useSession } from "@/entities/session";
import {
  useInterveneMutation,
  useReturnToBotMutation,
  useSendHumanMessageMutation,
  type TargetRoute,
} from "@/entities/handoff";
import { ConfirmPaymentAction } from "./ConfirmPaymentAction";

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

  const sendMessage = useSendHumanMessageMutation(chatId);

  const onSend = () => {
    const value = text.trim();
    if (!value || !chatId || sendMessage.isPending) return;
    sendMessage.mutate(
      { text: value },
      { onSuccess: () => setText("") },
    );
  };

  return (
    <div className="composer">
      <div className="composer-tools">
        <span className="interv-on">
          <Icon.user />
          Intervenido por ti · Bot pausado
        </span>
        <span className="tb-sep" />
        <button className="tb-btn" title="Plantillas">
          <Icon.template />
        </button>
        <button className="tb-btn" title="Adjuntar">
          <Icon.attach />
        </button>
        <button className="tb-btn" title="Audio">
          <Icon.mic />
        </button>
        <span className="tb-sep" />
        <button className="tb-btn" title="Asistente IA">
          <Icon.wand />
        </button>
        <button className="tb-btn" title="Emoji">
          <Icon.emoji />
        </button>
        <span className="right">
          {pendingPaymentOrderId && (
            <ConfirmPaymentAction orderId={pendingPaymentOrderId} />
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
      <div className="composer-row">
        <textarea
          placeholder="Escribe un mensaje al cliente…"
          rows={1}
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              (e.metaKey || e.ctrlKey) &&
              !e.shiftKey
            ) {
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
