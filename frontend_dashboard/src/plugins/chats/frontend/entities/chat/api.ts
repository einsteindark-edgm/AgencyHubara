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
} from "@plugins/chats/frontend/entities/session";
import {
  getMessageSender,
  isVisibleChatMessage,
  type ChatMessage,
} from "@plugins/chats/frontend/entities/message";
import { formatHourMinute } from "@/shared/lib";
import { env } from "@/shared/config";
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

/**
 * Bridge backend ↔ inbox: los tags que escribe el LLM via
 * `manage_conversation_tag` (`COMPRA_EXITOSA`, `RECHAZO`,
 * `CONFIRMADO_SIN_DATOS`, `INTERESADO`, `NO_ETIQUETADO`) NO matchean 1:1 con
 * los filtros del inbox del dashboard (`HUMANO`, `INTERESADO`, `PENDIENTE`,
 * `CLIENTE`, `REMARKETING`, `FRÍO`).
 *
 * Antes del fix: cualquier tag fuera del set inbox caía al fallback `FRÍO`
 * — bug reportado: venta ya procesada (`COMPRA_EXITOSA`) aparecía como Frío
 * en el inbox porque `COMPRA_EXITOSA` no estaba en KNOWN_TAGS.
 *
 * Mapeo semántico:
 *   - `COMPRA_EXITOSA` → `CLIENTE` (ya compró, es cliente real)
 *   - `RECHAZO` → `FRÍO` (no quiso comprar — Frío real, no fallback)
 *   - `CONFIRMADO_SIN_DATOS` → `PENDIENTE` (confirmó pero falta envío)
 *   - `INTERESADO` → `INTERESADO` (cliente activo en negociación)
 *   - `NO_ETIQUETADO` → `PENDIENTE` (conversación nueva sin clasificar)
 *   - `HUMANO` → `HUMANO`
 *
 * El estado REMARKETING NO viene del tag (manage_conversation_tag no lo
 * emite). Se deriva de `active_agent_route === "remarketing"` en
 * `normalizeTag` antes de mirar el tag.
 */
const BACKEND_TO_INBOX_TAG: Record<string, ChatTag> = {
  // Tags emitidos por el backend Python (manage_conversation_tag + defaults)
  INTERESADO: "INTERESADO",
  COMPRA_EXITOSA: "CLIENTE",
  RECHAZO: "FRÍO",
  CONFIRMADO_SIN_DATOS: "PENDIENTE",
  // HU "verificación humana de pago" (operativo hasta tener pasarela):
  // orden registrada en Medusa, falta verificación humana del pago. El
  // chat queda en cola humana (`escalate_to_human("PAYMENT_VERIFICATION_PENDING")`
  // dispara `active_agent_route="humano"` que ya re-rutea al filtro HUMANO),
  // pero si por alguna razón el `tag` queda CONFIRMADO_PAGO_PENDIENTE sin
  // el route flip, lo mostramos como PENDIENTE en el inbox.
  CONFIRMADO_PAGO_PENDIENTE: "PENDIENTE",
  NO_ETIQUETADO: "PENDIENTE",
  HUMANO: "HUMANO",
  // Aliases / tags ya alineados (seeds del prototipo o vistas legacy)
  PENDIENTE: "PENDIENTE",
  CLIENTE: "CLIENTE",
  REMARKETING: "REMARKETING",
  FRÍO: "FRÍO",
  FRIO: "FRÍO",
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

function normalizeTag(
  rawTag: string,
  activeAgentRoute?: string,
): { tag: ChatTag; tagClass: string } {
  // 1. Route override: si el dispatcher movió la sesión a remarketing, el tag
  // semántico para el inbox es REMARKETING (aunque el LLM no lo emita).
  if (activeAgentRoute === "remarketing") {
    return { tag: "REMARKETING", tagClass: TAG_CLASS.REMARKETING };
  }
  // 2. Mapear tag backend → tag inbox. Case-insensitive para tolerar
  // seeds antiguos.
  const upper = (rawTag ?? "").toUpperCase();
  const mapped = BACKEND_TO_INBOX_TAG[upper];
  if (mapped) {
    return { tag: mapped, tagClass: TAG_CLASS[mapped] ?? "t-pen" };
  }
  // 3. Fallback verdadero: tag desconocido → PENDIENTE (no FRÍO).
  // FRÍO ahora tiene semántica real ("cliente rechazó"); mandar lo
  // desconocido al PENDIENTE evita reportes falsos de churn.
  return { tag: "PENDIENTE", tagClass: "t-pen" };
}

function adaptSession(s: ChatSession): ChatInboxItem {
  const { tag, tagClass } = normalizeTag(s.tag, s.active_agent_route);
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

/** El backend escribe `image_url` como ref relativa (`/api/dashboard/media/...`);
 *  la volvemos absoluta con la base del API para usarla directo en `<img src>`.
 *  Si ya viniera absoluta (http…), la respetamos. */
function toMediaUrl(ref: string): string {
  return ref.startsWith("http") ? ref : `${env.apiUrl}${ref}`;
}

function adaptMessage(m: ChatMessage): ChatMessageItem {
  // Envío no-textual del bot (catálogo, flow, botones, galería…): el backend
  // persiste un marker human-readable y acá se pinta como nota de sistema —
  // sin esto el operador ve huecos y no puede seguir la conversación.
  if (m.ui_type === "ui_component_sent") {
    return {
      kind: "system",
      text: m.content ?? "",
      time:
        typeof m.timestamp === "string"
          ? new Date(m.timestamp).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })
          : undefined,
    };
  }
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
    imageUrl: m.image_url ? toMediaUrl(m.image_url) : undefined,
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
export { useSessionsStream } from "@plugins/chats/frontend/entities/session";
