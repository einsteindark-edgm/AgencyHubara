/**
 * Notificación al operador cuando una conversación pasa a manos del humano.
 *
 * Estrategia (sin tienda / sin FCM todavía): el inbox ya se refresca en tiempo
 * real por el SSE (`useSessionsStream` → sampler del vault → invalidate). Este
 * hook observa ESA misma data y detecta transiciones `bot → humano` — cubre
 * tanto el escalamiento del bot (`escalate_to_human`, que ocurre en el worker
 * y llega vía sampler) como un intervene hecho desde otro dispositivo. Al
 * detectar una, dispara una notificación del sistema (Tauri en la app,
 * Web Notification en browser).
 *
 * Limitación conocida y aceptada para esta fase: con la app CERRADA (proceso
 * muerto) no hay SSE → no hay notificación. Eso lo resuelve FCM (fase 2,
 * documentado en MOBILE_ANDROID_RUNBOOK.md §notificaciones).
 *
 * `diffNewHandoffs` es puro y testeable; el hook solo orquesta.
 */

import { useEffect, useRef } from "react";

import {
  ensureNotificationPermission,
  sendSystemNotification,
} from "@/shared/lib";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

interface HandoffDiff {
  /** Chats que ACABAN de pasar a humano (no estaban en humano en la foto previa). */
  newlyAssigned: Pick<ChatInboxItem, "id" | "name">[];
  /** True cuando no había foto previa (primera carga) — no se notifica nada. */
  isFirstSnapshot: boolean;
  /** La foto nueva id → estaEnHumano para la próxima comparación. */
  nextSnapshot: Map<string, boolean>;
}

export function diffNewHandoffs(
  prev: Map<string, boolean>,
  items: Pick<ChatInboxItem, "id" | "name" | "human">[],
): HandoffDiff {
  const nextSnapshot = new Map<string, boolean>();
  for (const it of items) nextSnapshot.set(it.id, it.human === true);

  // Primera carga: solo sembramos la foto. Notificar acá sería una ráfaga de
  // notificaciones viejas cada vez que se abre la app.
  if (prev.size === 0) {
    return { newlyAssigned: [], isFirstSnapshot: true, nextSnapshot };
  }

  const newlyAssigned = items.filter(
    (it) => it.human === true && prev.get(it.id) !== true,
  );
  return { newlyAssigned, isFirstSnapshot: false, nextSnapshot };
}

/**
 * Monta el observador de handoffs sobre la lista del inbox (ya reactiva por
 * SSE). Notifica solo cuando la app NO está visible/enfocada — si el operador
 * está mirando el inbox, la fila ya se pintó de HUMANO sola y una notificación
 * del sistema sería ruido.
 */
export function useHandoffNotifications(
  items: Pick<ChatInboxItem, "id" | "name" | "human">[],
  opts?: { onOpenChat?: (chatId: string) => void },
): void {
  const snapshotRef = useRef<Map<string, boolean>>(new Map());
  const onOpenChatRef = useRef(opts?.onOpenChat);
  useEffect(() => {
    onOpenChatRef.current = opts?.onOpenChat;
  });

  // PM2-M2: pedir el permiso runtime (Android 13+ POST_NOTIFICATIONS) UNA vez
  // en FOREGROUND, al montar. Sin esto, el primer `requestPermission` ocurría
  // recién al notificar — y como solo notificamos con la app en background, el
  // diálogo del sistema no se puede mostrar → denied → todas las
  // notificaciones se perdían en silencio.
  useEffect(() => {
    void ensureNotificationPermission();
  }, []);

  useEffect(() => {
    const { newlyAssigned, isFirstSnapshot, nextSnapshot } = diffNewHandoffs(
      snapshotRef.current,
      items,
    );
    // Solo avanzamos la foto cuando hay data (una lista vacía transitoria del
    // refetch no debe borrar la foto y re-disparar todo después).
    if (items.length > 0 || isFirstSnapshot) {
      snapshotRef.current = nextSnapshot;
    }
    if (isFirstSnapshot || newlyAssigned.length === 0) return;

    const appVisible =
      typeof document !== "undefined" &&
      document.visibilityState === "visible" &&
      document.hasFocus();
    if (appVisible) return;

    for (const chat of newlyAssigned) {
      // PM2-M7: tocar la notificación (fallback web) abre ESE chat — sin esto
      // el operador tenía que buscar la conversación a mano en la bandeja.
      void sendSystemNotification(
        "Conversación asignada",
        `${chat.name} necesita atención humana`,
        { onClick: () => onOpenChatRef.current?.(chat.id) },
      );
    }
  }, [items]);
}
