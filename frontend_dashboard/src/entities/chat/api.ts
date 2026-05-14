/**
 * `entities/chat` es el ADAPTADOR entre el backend real (`entities/session`)
 * y el modelo del prototipo Agency Desktop. Cada hook de chat (`useChatInbox`,
 * `useChatMessages`, `useChatMemory`, `useChatRoutingLog`) envuelve la query
 * subyacente de session y proyecta los campos al shape que esperan las
 * features de Chats — así el UI del rediseño consume datos productivos sin
 * tocar el contrato HTTP.
 *
 * El SSE de `useSessionsStream()` también se re-exporta para que la página
 * lo monte UNA vez.
 *
 * Notas no resueltas por el backend (siguen como vacíos):
 *   - `useChatNotes`: el backend no expone notas internas todavía.
 *   - `useChatFiles`: tampoco hay endpoint de archivos.
 */

import { useMemo } from "react";
import {
  useSession,
  useSessions,
  type ChatSession,
  type SessionDetails,
  type StatusHistoryEntry,
} from "@/entities/session";
import {
  getMessageSender,
  isVisibleChatMessage,
  type ChatMessage,
} from "@/entities/message";
import { formatHourMinute } from "@/shared/lib";
import type {
  AvatarColor,
  ChatInboxItem,
  ChatMessageItem,
  ChatTag,
  FileItem,
  MemoryItem,
  NoteItem,
  RoutingLogItem,
} from "./model";

/* ── Adapters internos ─────────────────────────────────────────────── */

const KNOWN_TAGS: ChatTag[] = [
  "HUMANO", "INTERESADO", "PENDIENTE", "CLIENTE", "REMARKETING", "FRÍO",
];

/** Mapa de tag canónico → clase CSS del prototipo (`t-int`, `t-cli`, …). */
const TAG_CLASS: Record<string, string> = {
  HUMANO:      "t-human",
  INTERESADO:  "t-int",
  PENDIENTE:   "t-pen",
  CLIENTE:     "t-cli",
  REMARKETING: "t-rem",
  FRÍO:        "t-cold",
  FRIO:        "t-cold",
};

const AVATAR_COLORS: AvatarColor[] = ["purple", "blue", "green", "orange", "pink", "teal"];

/** Hash determinista (djb2) para asignar avatar color a partir del id. */
function hashColor(id: string): AvatarColor {
  let h = 5381;
  for (let i = 0; i < id.length; i++) h = ((h << 5) + h + id.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

function shortInitials(phone: string): string {
  const trimmed = phone.replace(/\s+/g, "");
  // Tomar los últimos 2 caracteres "razonables" — para números reales son los
  // dos últimos dígitos, suficiente como ícono compacto en la lista.
  return trimmed.slice(-2).toUpperCase();
}

function normalizeTag(raw: string): { tag: ChatTag; tagClass: string } {
  const upper = raw.toUpperCase();
  if (KNOWN_TAGS.includes(upper as ChatTag)) {
    return {
      tag: upper as ChatTag,
      tagClass: TAG_CLASS[upper] ?? "t-cold",
    };
  }
  // Estados que no son del set canónico — el prototipo los pinta con look
  // de tag genérico (sin color). Etiquetamos como FRÍO sólo para fines de
  // filtrado y dejamos la clase neutra.
  return { tag: "FRÍO", tagClass: "t-cold" };
}

function adaptSession(s: ChatSession): ChatInboxItem {
  const { tag, tagClass } = normalizeTag(s.tag);
  return {
    id: s.session_id,
    name: s.phone_number,
    short: shortInitials(s.phone_number),
    snippet: s.motivo || "Sin diagnóstico…",
    time: formatHourMinute(s.last_updated_timestamp),
    tag,
    tagClass,
    color: hashColor(s.session_id),
    presence: "online",
    unread: 0,
    pinned: false,
    human: tag === "HUMANO",
    handoffReason: tag === "HUMANO" ? s.motivo : undefined,
  };
}

function adaptMessage(m: ChatMessage): ChatMessageItem {
  const sender = getMessageSender(m);
  // sender: "user"|"agent"|"human"
  //   user   → bubble inbound (cliente)
  //   agent  → bubble outbound del bot
  //   human  → bubble outbound del humano operador (badge distinto)
  const isOutbound = sender !== "user";
  return {
    kind: isOutbound ? "out" : "in",
    text: m.content ?? "",
    time:
      typeof m.timestamp === "number"
        ? formatHourMinute(m.timestamp)
        : typeof m.timestamp === "string"
          ? new Date(m.timestamp).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })
          : "",
    status: isOutbound ? "read" : undefined,
    author: isOutbound ? (sender === "human" ? "human" : "bot") : undefined,
  };
}

function adaptStatusEntry(e: StatusHistoryEntry): RoutingLogItem {
  const tagUpper = e.tag.toUpperCase();
  const palette: Record<string, RoutingLogItem["tagClass"]> = {
    INTERESADO: "purple",
    REMARKETING: "purple",
    PENDIENTE: "orange",
    NUEVO: "orange",
  };
  const tagClass = palette[tagUpper] ?? "";
  return {
    color: tagClass,
    agent: e.active_route,
    tag: e.tag,
    tagClass,
    time: new Date(e.timestamp * 1000).toLocaleString([], {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }),
    body: e.motivo || `Ruta activa: ${e.active_route}`,
  };
}

/* ── Hooks públicos ────────────────────────────────────────────────── */

export function useChatInbox() {
  const q = useSessions();
  const data = useMemo<ChatInboxItem[]>(
    () => (q.data ?? []).map(adaptSession),
    [q.data],
  );
  return { ...q, data };
}

export function useChatMessages(id: string | null) {
  const q = useSession(id);
  const data = useMemo<ChatMessageItem[]>(() => {
    if (!q.data) return [];
    return buildMessageList(q.data);
  }, [q.data]);
  return { ...q, data };
}

function buildMessageList(d: SessionDetails): ChatMessageItem[] {
  const items: ChatMessageItem[] = [];
  const visible = d.messages.filter(isVisibleChatMessage);
  if (visible.length > 0) {
    items.push({ kind: "day", text: "Conversación" });
  }
  for (const m of visible) items.push(adaptMessage(m));
  return items;
}

export function useChatMemory(id: string | null) {
  const q = useSession(id);
  const data = useMemo<MemoryItem[]>(() => {
    const text = q.data?.memory_content?.trim();
    if (!text) return [];
    return [
      {
        key: "Memoria de la IA",
        value: q.data?.active_agent_route ?? "agent",
        body: text,
      },
    ];
  }, [q.data]);
  return { ...q, data };
}

export function useChatRoutingLog(id: string | null) {
  const q = useSession(id);
  const data = useMemo<RoutingLogItem[]>(() => {
    const entries = q.data?.status_history ?? [];
    // El backend devuelve cronológico ascendente; el inspector espera más
    // reciente primero.
    return [...entries].reverse().map(adaptStatusEntry);
  }, [q.data]);
  return { ...q, data };
}

/** Notas internas — sin backend todavía. Mantiene la forma del hook para que
 *  la feature no cambie cuando aterrice el endpoint. */
export function useChatNotes(_id: string | null) {
  const data = useMemo<NoteItem[]>(() => [], []);
  return { data, isLoading: false, isError: false } as const;
}

/** Archivos — sin backend todavía. */
export function useChatFiles(_id: string | null) {
  const data = useMemo<FileItem[]>(() => [], []);
  return { data, isLoading: false, isError: false } as const;
}

/* Re-export del SSE para que la página lo monte sin importar directamente
 * de session — mantiene un único punto de entrada `entities/chat`. */
export { useSessionsStream } from "@/entities/session";
