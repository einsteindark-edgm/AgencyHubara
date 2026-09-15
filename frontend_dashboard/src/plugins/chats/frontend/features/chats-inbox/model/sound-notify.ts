/**
 * Sonido de la bandeja cuando escribe un cliente.
 *
 * Observa la misma lista del inbox que ya es reactiva por SSE (igual que
 * `handoff-notify`) y compara `lastInboundMs` / `human` contra la foto previa:
 *   - "message": inbound nuevo en un chat que atiende el bot.
 *   - "human":   inbound nuevo en un chat asignado al humano, o un chat que
 *                ACABA de pasar a humano. Gana sobre "message".
 * Un solo sonido por tick: una ráfaga de 5 mensajes no son 5 dings encimados.
 *
 * A diferencia de la notificación del sistema, suena también con el dashboard
 * en foco — el operador puede estar mirando otro chat.
 *
 * Limitación: la suscripción al stream de sesiones vive en la sección Chats
 * (INV-1) — con otra sección abierta no hay sonido.
 *
 * `decideChatSound` es puro y testeable; el hook solo orquesta.
 */

import { useEffect, useRef, useState } from "react";

import { installAudioUnlock, isAudioUnlocked, playSound } from "@/shared/lib";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

import { readSoundPrefs, type SoundChoice, type SoundEvent } from "./sound-prefs";

export type SoundSnapshot = Map<string, { lastInboundMs: number | null; human: boolean }>;

type Item = Pick<ChatInboxItem, "id" | "human" | "lastInboundMs">;

export function decideChatSound(
  prev: SoundSnapshot,
  items: Item[],
): { sound: SoundEvent | null; nextSnapshot: SoundSnapshot } {
  // Lista vacía transitoria del refetch: no borrar la foto (si no, el próximo
  // snapshot parecería "primera carga" y se perdería un sonido real).
  if (items.length === 0 && prev.size > 0) return { sound: null, nextSnapshot: prev };

  const nextSnapshot: SoundSnapshot = new Map();
  for (const it of items) {
    nextSnapshot.set(it.id, { lastInboundMs: it.lastInboundMs, human: it.human === true });
  }
  if (prev.size === 0) return { sound: null, nextSnapshot };

  let sound: SoundEvent | null = null;
  for (const it of items) {
    const before = prev.get(it.id);
    const human = it.human === true;
    const newInbound =
      it.lastInboundMs !== null &&
      (before === undefined || before.lastInboundMs === null || it.lastInboundMs > before.lastInboundMs);
    const newlyHuman = human && before !== undefined && !before.human;
    if ((newInbound && human) || newlyHuman) return { sound: "human", nextSnapshot };
    if (newInbound) sound = "message";
  }
  return { sound, nextSnapshot };
}

export function toSoundSource(choice: SoundChoice) {
  return choice.kind === "preset"
    ? ({ kind: "preset", id: choice.id } as const)
    : ({ kind: "custom", dataUrl: choice.dataUrl } as const);
}

/** Reproduce el sonido de un evento con las preferencias vigentes. */
export function playChatSound(event: SoundEvent): Promise<boolean> {
  const prefs = readSoundPrefs();
  return playSound(toSoundSource(prefs[event]), prefs.volume);
}

/**
 * Monta el sonido sobre la lista del inbox. Devuelve `audioBlocked`: true
 * mientras el navegador no permita audio (falta un click en la página) — la UI
 * lo muestra para que el operador no crea que está sonando.
 */
export function useChatSoundNotifications(items: Item[]): { audioBlocked: boolean } {
  const snapshotRef = useRef<SoundSnapshot>(new Map());
  const [unlocked, setUnlocked] = useState(isAudioUnlocked);

  useEffect(() => installAudioUnlock(() => setUnlocked(true)), []);

  useEffect(() => {
    const { sound, nextSnapshot } = decideChatSound(snapshotRef.current, items);
    snapshotRef.current = nextSnapshot;
    if (sound === null || !readSoundPrefs().enabled) return;
    void playChatSound(sound);
  }, [items]);

  return { audioBlocked: !unlocked };
}
