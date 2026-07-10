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
 * El reducer (`outboxReducer`) es puro y testeable; el hook (`useOutbox`)
 * orquesta el I/O (compresión, XHR con progreso, mutación de envío) y refresca
 * la query del chat al éxito para que aparezca la burbuja real del servidor.
 */

import { useCallback, useReducer, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
      // Se remueve: la burbuja real (refetch del servidor) lo reemplaza.
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

/** UUID robusto: `crypto.randomUUID` cuando existe, fallback simple si no. */
function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `m-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

export function useOutbox(chatId: string | null) {
  const [state, dispatch] = useReducer(outboxReducer, { items: [] });
  const qc = useQueryClient();
  // Los blobs comprimidos viven fuera del reducer (no serializables): map por id.
  const blobs = useRef<Map<string, { blob: Blob; caption: string }>>(new Map());

  const invalidate = useCallback(() => {
    if (chatId) qc.invalidateQueries({ queryKey: sessionKeys.detail(chatId) });
  }, [chatId, qc]);

  // Envío (fase B): con el attachment ya en Meta, dispara el mensaje.
  const runSend = useCallback(
    async (id: string, attachmentId: string, caption: string) => {
      if (!chatId) return;
      try {
        const body: SendHumanMessageInput = {
          attachment_id: attachmentId,
          text: caption || undefined,
          client_message_id: id,
        };
        await apiClient.post(
          `/api/dashboard/sessions/${chatId}/messages`,
          body,
        );
        dispatch({ type: "sent", id });
        blobs.current.delete(id);
        invalidate();
      } catch (e) {
        dispatch({
          type: "failed",
          id,
          error: e instanceof Error ? e.message : "no se pudo enviar la foto",
        });
      }
    },
    [chatId, invalidate],
  );

  // Subida (fase A) + envío.
  const runUploadAndSend = useCallback(
    async (id: string, blob: Blob, caption: string) => {
      if (!chatId) return;
      try {
        dispatch({ type: "compressed", id });
        const res = await uploadHumanMedia(chatId, blob, `${id}.jpg`, (f) =>
          dispatch({ type: "progress", id, fraction: f }),
        );
        dispatch({ type: "uploaded", id, attachmentId: res.attachment_id });
        await runSend(id, res.attachment_id, caption);
      } catch (e) {
        dispatch({
          type: "failed",
          id,
          error: e instanceof Error ? e.message : "no se pudo subir la foto",
        });
      }
    },
    [chatId, runSend],
  );

  /** Encola una o más fotos con un caption compartido. No bloquea. */
  const enqueue = useCallback(
    async (files: File[], caption: string) => {
      for (const file of files) {
        const id = newId();
        try {
          const { blob, previewUrl } = await compressImage(file);
          blobs.current.set(id, { blob, caption });
          dispatch({ type: "enqueue", id, previewUrl, caption });
          void runUploadAndSend(id, blob, caption);
        } catch (e) {
          dispatch({ type: "enqueue", id, previewUrl: "", caption });
          dispatch({
            type: "failed",
            id,
            error: e instanceof Error ? e.message : "no se pudo procesar la foto",
          });
        }
      }
    },
    [runUploadAndSend],
  );

  /** Reintenta un item fallido reusando el attachment si ya se había subido. */
  const retry = useCallback(
    (id: string) => {
      const item = state.items.find((it) => it.id === id);
      if (!item) return;
      dispatch({ type: "retry", id });
      if (item.attachmentId) {
        void runSend(id, item.attachmentId, item.caption);
      } else {
        const cached = blobs.current.get(id);
        if (cached) void runUploadAndSend(id, cached.blob, cached.caption);
      }
    },
    [state.items, runSend, runUploadAndSend],
  );

  const remove = useCallback((id: string) => {
    dispatch({ type: "remove", id });
    blobs.current.delete(id);
  }, []);

  return { items: state.items, enqueue, retry, remove };
}
