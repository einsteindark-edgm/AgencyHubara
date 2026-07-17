/**
 * Outbox optimista de fotos del operador.
 *
 * Objetivo de diseño (anti-cuelgue): mandar una foto NUNCA bloquea el composer.
 * Cada envío es un item con su propia máquina de estados que corre en segundo
 * plano; el operador puede seguir escribiendo o encolar más fotos. El pipeline
 * es de dos fases des-acopladas: comprimir → subir (fase A, pesada) → enviar
 * (fase B). Un retry tras un fallo de envío REUSA el `attachmentId` — nunca
 * re-sube los bytes.
 *
 * Post-premortem (PM-F3): el estado vive en un store MODULE-LEVEL keyed por
 * chatId (no en el hook) — navegar a otro chat o volver al inbox NO pierde los
 * items en vuelo ni los fallidos; al volver, la tira sigue ahí. El hook se
 * suscribe con `useSyncExternalStore`.
 *
 * Otros fixes del premortem acá: revocación de blob URLs (PM-F1), retry tras
 * fallo de compresión reusa el File original (PM-F2), el item "sent" se
 * remueve DESPUÉS del refetch que trae la burbuja real (PM-F7, sin parpadeo),
 * y las subidas corren con concurrencia acotada (PM-F9, red celular).
 *
 * El reducer (`outboxReducer`) es puro y testeable; el resto orquesta I/O.
 */

import { useCallback, useSyncExternalStore } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { compressImage } from "@/shared/lib";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  uploadHumanMedia,
  type SendHumanMessageInput,
} from "@plugins/chats/frontend/entities/handoff";
import { apiClient } from "@/shared/api/client";

export type OutboxStatus =
  | "compressing"
  | "uploading"
  | "sending"
  | "sent"
  | "failed";

export interface OutboxItem {
  id: string; // = client_message_id (idempotencia)
  previewUrl: string; // blob local para la burbuja optimista
  caption: string;
  status: OutboxStatus;
  progress: number; // 0..1 durante uploading
  attachmentId?: string; // media_id de Meta una vez subido (retry no re-sube)
  error?: string;
}

export interface OutboxState {
  items: OutboxItem[];
}

export type OutboxAction =
  | { type: "enqueue"; id: string; previewUrl: string; caption: string }
  | { type: "compressed"; id: string }
  | { type: "progress"; id: string; fraction: number }
  | { type: "uploaded"; id: string; attachmentId: string }
  | { type: "sent"; id: string }
  | { type: "failed"; id: string; error: string }
  | { type: "retry"; id: string }
  | { type: "remove"; id: string };

function patch(
  state: OutboxState,
  id: string,
  fn: (it: OutboxItem) => OutboxItem,
): OutboxState {
  return { items: state.items.map((it) => (it.id === id ? fn(it) : it)) };
}

export function outboxReducer(
  state: OutboxState,
  action: OutboxAction,
): OutboxState {
  switch (action.type) {
    case "enqueue":
      return {
        items: [
          ...state.items,
          {
            id: action.id,
            previewUrl: action.previewUrl,
            caption: action.caption,
            status: "compressing",
            progress: 0,
          },
        ],
      };
    case "compressed":
      return patch(state, action.id, (it) => ({ ...it, status: "uploading" }));
    case "progress":
      return patch(state, action.id, (it) => ({
        ...it,
        progress: action.fraction,
      }));
    case "uploaded":
      return patch(state, action.id, (it) => ({
        ...it,
        status: "sending",
        attachmentId: action.attachmentId,
      }));
    case "sent":
      // Se remueve: la burbuja real del servidor ya está en la lista (el
      // orquestador espera el refetch antes de despachar esto — PM-F7).
      return { items: state.items.filter((it) => it.id !== action.id) };
    case "failed":
      return patch(state, action.id, (it) => ({
        ...it,
        status: "failed",
        error: action.error,
      }));
    case "retry":
      return patch(state, action.id, (it) => ({
        ...it,
        error: undefined,
        // Con attachment ya subido → reintenta el envío; si no → recomprime.
        status: it.attachmentId ? "sending" : "compressing",
      }));
    case "remove":
      return { items: state.items.filter((it) => it.id !== action.id) };
    default:
      return state;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Store module-level por chatId (PM-F3: sobrevive al remount/navegación)
// ═══════════════════════════════════════════════════════════════════════════

const EMPTY_STATE: OutboxState = { items: [] };

const states = new Map<string, OutboxState>();
const listeners = new Map<string, Set<() => void>>();
/** Blob comprimido por item (para reintentar la subida sin recomprimir). */
const compressedBlobs = new Map<string, { blob: Blob; caption: string }>();
/** File ORIGINAL por item (PM-F2: si la compresión falló, el retry parte de acá). */
const originalFiles = new Map<string, { file: File; caption: string }>();

function getState(chatId: string): OutboxState {
  return states.get(chatId) ?? EMPTY_STATE;
}

function dispatch(chatId: string, action: OutboxAction): void {
  const prev = getState(chatId);
  // PM-F1: revocar el object URL en los estados terminales — sin esto cada
  // foto enviada deja un Blob retenido de por vida en el webview.
  if (action.type === "sent" || action.type === "remove") {
    const item = prev.items.find((it) => it.id === action.id);
    if (item?.previewUrl) {
      try {
        URL.revokeObjectURL(item.previewUrl);
      } catch {
        /* jsdom / entornos sin soporte */
      }
    }
    compressedBlobs.delete(action.id);
    originalFiles.delete(action.id);
  }
  states.set(chatId, outboxReducer(prev, action));
  for (const fn of listeners.get(chatId) ?? []) fn();
}

function subscribe(chatId: string, fn: () => void): () => void {
  let set = listeners.get(chatId);
  if (!set) {
    set = new Set();
    listeners.set(chatId, set);
  }
  set.add(fn);
  return () => {
    set.delete(fn);
  };
}

// ── Limitador de concurrencia de subidas (PM-F9) ───────────────────────────
// En red celular, 20 XHR paralelos saturan el uplink y TODAS las barras se
// arrastran. Máx 3 subidas en vuelo; el resto espera su turno.

const MAX_CONCURRENT_UPLOADS = 3;
let activeUploads = 0;
const uploadQueue: Array<() => void> = [];

async function withUploadSlot<T>(fn: () => Promise<T>): Promise<T> {
  if (activeUploads >= MAX_CONCURRENT_UPLOADS) {
    await new Promise<void>((resolve) => uploadQueue.push(resolve));
  }
  activeUploads += 1;
  try {
    return await fn();
  } finally {
    activeUploads -= 1;
    uploadQueue.shift()?.();
  }
}

// ── Orquestación (módulo, no hook: los envíos sobreviven al unmount) ───────

async function runSend(
  chatId: string,
  qc: QueryClient,
  id: string,
  attachmentId: string,
  caption: string,
): Promise<void> {
  try {
    const body: SendHumanMessageInput = {
      attachment_id: attachmentId,
      text: caption || undefined,
      client_message_id: id,
    };
    await apiClient.post(`/api/dashboard/sessions/${chatId}/messages`, body);
    // PM-F7: esperar el refetch ANTES de remover el item optimista — así la
    // burbuja real del servidor ya está pintada cuando la preview desaparece
    // (sin ventana de "la foto se esfumó").
    await qc.invalidateQueries({ queryKey: sessionKeys.detail(chatId) });
    dispatch(chatId, { type: "sent", id });
  } catch (e) {
    dispatch(chatId, {
      type: "failed",
      id,
      error: e instanceof Error ? e.message : "no se pudo enviar la foto",
    });
  }
}

async function runUploadAndSend(
  chatId: string,
  qc: QueryClient,
  id: string,
  blob: Blob,
  caption: string,
): Promise<void> {
  try {
    dispatch(chatId, { type: "compressed", id });
    const res = await withUploadSlot(() =>
      uploadHumanMedia(chatId, blob, `${id}.jpg`, (f) =>
        dispatch(chatId, { type: "progress", id, fraction: f }),
      ),
    );
    dispatch(chatId, { type: "uploaded", id, attachmentId: res.attachment_id });
    await runSend(chatId, qc, id, res.attachment_id, caption);
  } catch (e) {
    dispatch(chatId, {
      type: "failed",
      id,
      error: e instanceof Error ? e.message : "no se pudo subir la foto",
    });
  }
}

async function compressAndRun(
  chatId: string,
  qc: QueryClient,
  id: string,
  file: File,
  caption: string,
): Promise<void> {
  try {
    const { blob, previewUrl } = await compressImage(file);
    compressedBlobs.set(id, { blob, caption });
    dispatch(chatId, { type: "enqueue", id, previewUrl, caption });
    void runUploadAndSend(chatId, qc, id, blob, caption);
  } catch (e) {
    // La compresión falló (HEIC renombrado, archivo corrupto). El item queda
    // visible en failed; el File original quedó cacheado ANTES (PM-F2), así
    // que Reintentar re-corre la compresión desde ahí.
    dispatch(chatId, { type: "enqueue", id, previewUrl: "", caption });
    dispatch(chatId, {
      type: "failed",
      id,
      error:
        e instanceof Error
          ? `no se pudo procesar la imagen (${e.message})`
          : "no se pudo procesar la imagen",
    });
  }
}

/** UUID robusto: `crypto.randomUUID` cuando existe, fallback simple si no. */
function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `m-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

export function useOutbox(chatId: string | null) {
  const qc = useQueryClient();
  const key = chatId ?? "";

  const items = useSyncExternalStore(
    useCallback((fn: () => void) => subscribe(key, fn), [key]),
    useCallback(() => getState(key).items, [key]),
  );

  /** Encola una o más fotos. El caption va SOLO en la primera del lote
   *  (PM-F11: cada foto es un mensaje de WhatsApp separado — repetir el texto
   *  en las 3 fotos le llega triplicado al cliente). No bloquea. */
  const enqueue = useCallback(
    async (files: File[], caption: string) => {
      if (!chatId) return;
      let idx = 0;
      for (const file of files) {
        const id = newId();
        const itemCaption = idx === 0 ? caption : "";
        idx += 1;
        // Cachear el original ANTES de intentar comprimir (PM-F2).
        originalFiles.set(id, { file, caption: itemCaption });
        await compressAndRun(chatId, qc, id, file, itemCaption);
      }
    },
    [chatId, qc],
  );

  /** Reintenta un item fallido: con attachment reintenta el send; con blob
   *  comprimido re-sube; si solo queda el File original, recomprime. */
  const retry = useCallback(
    (id: string) => {
      if (!chatId) return;
      const item = getState(key).items.find((it) => it.id === id);
      if (!item) return;
      dispatch(chatId, { type: "retry", id });
      if (item.attachmentId) {
        void runSend(chatId, qc, id, item.attachmentId, item.caption);
        return;
      }
      const cachedBlob = compressedBlobs.get(id);
      if (cachedBlob) {
        void runUploadAndSend(chatId, qc, id, cachedBlob.blob, cachedBlob.caption);
        return;
      }
      const original = originalFiles.get(id);
      if (original) {
        // Re-corre TODO el pipeline desde el File original. El item ya existe,
        // así que removemos el placeholder anterior para no duplicar la tira.
        dispatch(chatId, { type: "remove", id });
        originalFiles.set(id, original); // remove lo limpió — restaurar
        void compressAndRun(chatId, qc, id, original.file, original.caption);
        return;
      }
      // Sin nada que reintentar (no debería pasar): marcar como no reintenable.
      dispatch(chatId, {
        type: "failed",
        id,
        error: "no hay datos para reintentar — descartá y volvé a adjuntar",
      });
    },
    [chatId, key, qc],
  );

  const remove = useCallback(
    (id: string) => {
      if (!chatId) return;
      dispatch(chatId, { type: "remove", id });
    },
    [chatId],
  );

  return { items, enqueue, retry, remove };
}
