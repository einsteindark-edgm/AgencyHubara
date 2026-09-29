/**
 * No leídos de la bandeja, como WhatsApp: cuántos mensajes del cliente
 * llegaron desde la última vez que ESTE operador abrió el chat. No importa
 * quién habló último — un "gracias, hasta luego" del cliente tras la
 * respuesta del bot sigue siendo un no leído hasta que alguien lo abra.
 *
 * El backend da el total de mensajes del cliente (`inboundCount`); acá se
 * recuerda cuántos había al abrir cada chat. Estado del puesto (localStorage,
 * por navegador) — igual que las preferencias de sonido.
 *
 * Primera carga: todo lo que ya existía cuenta como visto (si no, la bandeja
 * entera amanecería con contadores de conversaciones de hace semanas). Un
 * chat que aparece DESPUÉS de esa línea base cuenta todos sus mensajes.
 */

import { useEffect, useMemo, useSyncExternalStore } from "react";

export interface SeenState {
  /** Ya se fijó la línea base con la primera bandeja cargada. */
  baseline: boolean;
  /** session_id → mensajes del cliente que había cuando se abrió. */
  seen: Record<string, number>;
}

export const EMPTY_SEEN: SeenState = { baseline: false, seen: {} };
export const SEEN_STORAGE_KEY = "hubara.chats.seen.v1";

interface Countable {
  id: string;
  inboundCount: number;
}

/** Aplica la línea base y marca como visto el chat abierto. Devuelve el MISMO
 *  objeto si nada cambió (para no re-persistir en cada tick del SSE). */
export function reconcileSeen(
  state: SeenState,
  chats: readonly Countable[],
  openId: string | null,
): SeenState {
  let seen = state.seen;
  let baseline = state.baseline;
  const set = (id: string, n: number) => {
    if (seen[id] === n) return;
    if (seen === state.seen) seen = { ...seen };
    seen[id] = n;
  };
  if (!baseline && chats.length > 0) {
    baseline = true;
    for (const c of chats) set(c.id, c.inboundCount);
  }
  const open = openId ? chats.find((c) => c.id === openId) : undefined;
  if (open) set(open.id, open.inboundCount);
  if (seen === state.seen && baseline === state.baseline) return state;
  return { baseline, seen };
}

export function unseenCount(state: SeenState, chat: Countable): number {
  const seen = state.seen[chat.id];
  return Math.max(0, chat.inboundCount - (seen ?? 0));
}

function readSeen(): SeenState {
  try {
    const raw = localStorage.getItem(SEEN_STORAGE_KEY);
    if (!raw) return EMPTY_SEEN;
    const parsed = JSON.parse(raw) as Partial<SeenState>;
    if (typeof parsed.baseline !== "boolean" || typeof parsed.seen !== "object" || !parsed.seen) {
      return EMPTY_SEEN;
    }
    return { baseline: parsed.baseline, seen: parsed.seen as Record<string, number> };
  } catch {
    return EMPTY_SEEN;
  }
}

let cached: SeenState | null = null;
const listeners = new Set<() => void>();

function writeSeen(state: SeenState): void {
  cached = state;
  try {
    localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // storage lleno / bloqueado: el contador sigue funcionando en memoria.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): SeenState {
  if (cached === null) cached = readSeen();
  return cached;
}

/** Los chats con `unread` resuelto para este operador, con el abierto marcado
 *  como visto. Se calcula en render (sin parpadeo) y se persiste en efecto. */
export function useUnseenChats<T extends Countable & { unread: number }>(
  chats: T[],
  openId: string | null,
): T[] {
  const stored = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY_SEEN);
  const state = reconcileSeen(stored, chats, openId);

  useEffect(() => {
    if (state !== stored) writeSeen(state);
  }, [state, stored]);

  return useMemo(
    () => chats.map((c) => ({ ...c, unread: unseenCount(state, c) })),
    [chats, state],
  );
}
