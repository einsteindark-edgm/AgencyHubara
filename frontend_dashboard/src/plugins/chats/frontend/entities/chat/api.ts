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
  type SessionOrderRef,
  type SessionOrigin,
  type SessionPostponed,
  type StatusHistoryEntry,
} from "@plugins/chats/frontend/entities/session";
import {
  getMessageSender,
  isVisibleChatMessage,
  type ChatMessage,
} from "@plugins/chats/frontend/entities/message";
import { bogotaDayIsoFromUnix, formatBogotaHourMinute } from "@/shared/lib";
import { env, getAccessToken } from "@/shared/config";
import type {
  AvatarColor,
  ChatInboxItem,
  ChatOrderBadge,
  ChatPostponedBadge,
  ChatMessageItem,
  ChatOverview,
  ChatQuote,
  ChatTag,
  FileItem,
  MemoryItem,
  NoteItem,
  QuoteAuthor,
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
  SIN_RESPUESTA: "t-noreply",
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
  // Escalera de reactivación agotada (2026-09-18): tag propio — si cayera al
  // fallback, el operador no podría filtrar a quienes nunca contestaron.
  SIN_RESPUESTA: "SIN_RESPUESTA",
  CONFIRMADO_SIN_DATOS: "PENDIENTE",
  // HU "verificación humana de pago" (operativo hasta tener pasarela):
  // orden registrada en Medusa, falta verificación humana del pago. El
  // chat queda en cola humana (`escalate_to_human("PAYMENT_VERIFICATION_PENDING")`
  // dispara `active_agent_route="humano"`, y `isAssignedToHuman` lo pone en
  // el filtro Humano por la ruta), pero si el `tag` queda
  // CONFIRMADO_PAGO_PENDIENTE sin el route flip, lo mostramos como PENDIENTE.
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

/** "Asignada al humano" es una propiedad de la RUTA, no del tag comercial.
 *
 *  `active_agent_route === "humano"` es lo que pausa al bot y habilita el
 *  composer del operador (`ChatsComposer`, `RouteState`). El tag puede
 *  cambiar mientras la ruta sigue en humano: `confirm_payment` desde Orders
 *  deja `COMPRA_EXITOSA` (y NO toca la ruta), y el LLM puede etiquetar
 *  `CONFIRMADO_PAGO_PENDIENTE` después de escalar. Derivar `human` del tag
 *  hacía que esas conversaciones desaparecieran del filtro "Asignadas al
 *  humano" aunque el header dijera "Intervenido · bot en pausa" (caso prod
 *  wa_573229041190, 2026-09-08). El tag HUMANO sigue contando por
 *  tolerancia a metadata legacy sin ruta. */
function isAssignedToHuman(s: ChatSession, tag: ChatTag): boolean {
  return s.active_agent_route === "humano" || tag === "HUMANO";
}

/**
 * `order_ref` del backend → chip de la fila.
 *
 * El chip solo existe para una ORDEN real con su número: sin `display_id` no
 * se pinta nada (la primera versión caía a los últimos 6 del id interno de
 * Medusa, que al operador no le dicen nada).
 *
 * El "#" se pone ACÁ, una sola vez. El backend manda el número pelado ("32"),
 * pero frontend y backend despliegan por separado: uno viejo manda "#32" (el
 * formato de la vista Orders) y el chip pintaba "##32" — por eso se pela igual.
 */
function adaptOrderRef(ref: SessionOrderRef | null | undefined): ChatOrderBadge | null {
  const number = ref?.display_id?.replace(/^#+/, "").trim();
  if (!ref || !number) return null;
  return {
    label: `#${number}`,
    orderId: ref.order_id,
    payment: ref.payment,
    count: ref.count,
  };
}

const WEEKDAYS_SHORT = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** El chip va CORTO (la fila es angosta: "Cita enviada mar 22 sep" no cabía
 *  junto a la etiqueta); la descripción completa va al tooltip y al texto
 *  accesible, con el día de la semana. */
const POSTPONED_TEXT: Record<
  ChatPostponedBadge["status"],
  { chip: string; describe: (day: string) => string }
> = {
  esperando: { chip: "Retoma", describe: (day) => `retoma el ${day}` },
  cita_pendiente: { chip: "Cita", describe: (day) => `la cita del ${day} todavía no salió` },
  cita_enviada: { chip: "Cita ✓", describe: (day) => `cita enviada el ${day}, esperando respuesta` },
  vencido: { chip: "Retomar", describe: (day) => `había que retomar el ${day}` },
};

/** Día de la retoma en hora Colombia: `{ short: "28 sep", long: "lun 28 sep" }`. */
function bogotaDay(ms: number): { short: string; long: string } {
  const iso = bogotaDayIsoFromUnix(Math.floor(ms / 1000));
  if (!iso) return { short: "", long: "" };
  const [y, m, d] = iso.split("-").map(Number);
  const short = `${d} ${MONTHS_SHORT[m - 1]}`;
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { short, long: `${WEEKDAYS_SHORT[weekday]} ${short}` };
}

/** `postponed` del backend → chip de la fila + clave del filtro "Pospuestos". */
function adaptPostponed(p: SessionPostponed | null | undefined): ChatPostponedBadge | null {
  if (!p) return null;
  const text = POSTPONED_TEXT[p.status];
  const day = bogotaDay(p.until_ms);
  const manual = p.kind === "manual";
  return {
    status: p.status,
    untilMs: p.until_ms,
    label: `${text.chip} ${day.short}`.trim(),
    description:
      manual && p.status === "esperando"
        ? `pospuesto por el equipo hasta el ${day.long}`
        : text.describe(day.long),
    text: p.text,
    overdue: p.overdue ?? false,
    manual,
  };
}

function adaptSession(s: ChatSession): ChatInboxItem {
  const { tag, tagClass } = normalizeTag(s.tag, s.active_agent_route);
  const human = isAssignedToHuman(s, tag);
  return {
    id: s.session_id,
    name: s.phone_number,
    short: shortInitials(s.phone_number),
    snippet: s.motivo || "Sin diagnóstico…",
    time: formatBogotaHourMinute(s.last_updated_timestamp),
    timestamp: s.last_updated_timestamp,
    dayIso: bogotaDayIsoFromUnix(s.last_updated_timestamp),
    lastInboundMs: s.last_inbound_ms ?? null,
    order: adaptOrderRef(s.order_ref),
    postponed: adaptPostponed(s.postponed),
    tag,
    tagClass,
    color: hashColor(s.session_id),
    presence: "online",
    unread: 0,
    pinned: false,
    human,
    handoffReason: human ? s.motivo : undefined,
  };
}

/** El backend escribe `image_url`/`document_url` como ref relativa
 *  (`/api/dashboard/media/...`); la volvemos absoluta con la base del API para
 *  usarla directo en `<img src>` / `<a href>`. Si ya viniera absoluta (http…),
 *  la respetamos.
 *
 *  Auth: ni `<img>` ni un `<a target=_blank>` pueden llevar el header Bearer —
 *  en prod (Cognito) el GET /media daría 401. `require_auth` acepta el JWT por
 *  query `access_token` (mismo patrón que el SSE), así que lo appendeamos
 *  cuando hay sesión. Sin token (local sin Cognito) la URL queda limpia. */
function toMediaUrl(ref: string): string {
  const abs = ref.startsWith("http") ? ref : `${env.apiUrl}${ref}`;
  // El token SOLO va a nuestro API. Las fotos del bot citadas en un reply
  // apuntan al CDN de assets (otro origen): appendear el JWT ahí lo filtraría
  // a un tercero.
  if (!abs.startsWith(`${env.apiUrl}/`)) return abs;
  const token = getAccessToken();
  if (!token) return abs;
  const sep = abs.includes("?") ? "&" : "?";
  return `${abs}${sep}access_token=${encodeURIComponent(token)}`;
}

/** El historial no garantiza un formato único de timestamp: los eventos nuevos
 *  traen unix epoch en SEGUNDOS y los viejos un ISO string. Normalizamos a
 *  segundos ACÁ, en un solo lugar, para que el resto del adaptador (hora, día,
 *  agrupación) hable un único idioma. `0` = sin timestamp utilizable. */
function toUnixSeconds(ts: ChatMessage["timestamp"]): number {
  if (typeof ts === "number") return Number.isFinite(ts) ? ts : 0;
  if (typeof ts === "string") {
    const ms = Date.parse(ts);
    return Number.isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }
  return 0;
}

const QUOTE_AUTHORS: readonly QuoteAuthor[] = ["user", "agent", "human"];

function adaptQuote(q: ChatMessage["reply_to"]): ChatQuote | undefined {
  if (!q) return undefined;
  const author = QUOTE_AUTHORS.find((a) => a === q.author) ?? "unknown";
  return {
    author,
    text: q.text || undefined,
    imageUrl: q.image_url ? toMediaUrl(q.image_url) : undefined,
  };
}

function adaptMessage(m: ChatMessage): ChatMessageItem {
  const unix = toUnixSeconds(m.timestamp);
  const dayIso = bogotaDayIsoFromUnix(unix) || undefined;
  // Envío no-textual del bot (catálogo, flow, botones, galería…): el backend
  // persiste un marker human-readable y acá se pinta como nota de sistema —
  // sin esto el operador ve huecos y no puede seguir la conversación.
  if (m.ui_type === "ui_component_sent") {
    // Los botones SÍ se pueden reconstruir como el mensaje real que recibió el
    // cliente (cuerpo + botones): dejan de ser una nota y vuelven a ser una
    // burbuja del bot. El resto de componentes no tiene forma que recuperar.
    if (m.event?.kind === "bot_buttons") {
      return {
        kind: "out",
        author: "bot",
        time: formatBogotaHourMinute(unix),
        dayIso,
        status: "read",
        event: m.event,
      };
    }
    return {
      kind: "system",
      text: m.content ?? "",
      time: formatBogotaHourMinute(unix) || undefined,
      dayIso,
      event: m.event,
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
    time: formatBogotaHourMinute(unix),
    dayIso,
    status: isOutbound ? "read" : undefined,
    author: isOutbound ? (sender === "human" ? "human" : "bot") : undefined,
    imageUrl: m.image_url ? toMediaUrl(m.image_url) : undefined,
    documentUrl: m.document_url ? toMediaUrl(m.document_url) : undefined,
    documentName: m.document_filename ?? undefined,
    replyTo: adaptQuote(m.reply_to),
    event: m.event,
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

/**
 * Aplana el historial insertando un separador por DÍA CALENDARIO colombiano,
 * como WhatsApp. Antes había un único separador literal "Conversación" para
 * todo el hilo: el operador no podía saber si un mensaje era de hoy o de hace
 * tres semanas sin abrir el inspector.
 *
 * El separador lleva `dayIso` (dato), no la etiqueta ya renderizada — "Hoy"
 * caduca a medianoche y esta lista vive en la cache de TanStack Query. La
 * etiqueta la produce `formatDayLabelEs` en el render de la burbuja.
 *
 * Mensajes sin timestamp (historial legacy) no abren ni cierran grupo: se
 * pintan bajo el separador vigente. Inventarles un día sería mentir.
 */
function buildMessageList(d: SessionDetails): ChatMessageItem[] {
  const items: ChatMessageItem[] = [];
  let currentDay: string | undefined;
  for (const m of d.messages.filter(isVisibleChatMessage)) {
    const item = adaptMessage(m);
    if (item.dayIso && item.dayIso !== currentDay) {
      currentDay = item.dayIso;
      items.push({ kind: "day", dayIso: item.dayIso });
    }
    items.push(item);
  }
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

/* ── Origen / cabecera del inspector ───────────────────────────────── */

const META_CHANNELS = new Set(["ad", "post"]);

/** Etiqueta humana del origen. Prioridad para Meta: nombre real de la
 *  campaña (Graph) > headline del referral > ad id. Los demás canales los
 *  clasifica el ingest (`_classify_origin_channel`). */
export function formatOrigin(origin: SessionOrigin | null | undefined): {
  label: string;
  detail?: string;
  isMeta: boolean;
} {
  if (!origin || !origin.channel) return { label: "Sin dato", isMeta: false };
  if (META_CHANNELS.has(origin.channel)) {
    const kind = origin.channel === "post" ? "Meta post" : "Meta Ads";
    const name = origin.campaign_name ?? origin.headline ?? origin.source_id ?? "campaña";
    const detail = origin.campaign_name
      ? origin.ad_name ?? origin.headline ?? undefined
      : origin.source_id
        ? `ad ${origin.source_id}`
        : undefined;
    return { label: `${kind} · ${name}`, detail: detail ?? undefined, isMeta: true };
  }
  if (origin.channel === "web_cart") return { label: "Carrito web", isMeta: false };
  if (origin.channel === "web_referral") {
    // Referral de anuncio SIN ctwa_clid: Meta lo omite cuando el cliente tocó
    // el anuncio desde navegador / Instagram web / WhatsApp Web (2026-09-14).
    // Sigue siendo el mismo anuncio (source_id = ad id): se etiqueta como tal
    // para que no parezca un origen distinto; sin source_id es un link web.
    if (origin.source_id) {
      const name = origin.campaign_name ?? origin.headline ?? origin.source_id;
      return {
        label: `Anuncio (web/desktop) · ${name}`,
        detail: origin.campaign_name ? origin.ad_name ?? origin.headline ?? undefined : `ad ${origin.source_id}`,
        isMeta: true,
      };
    }
    return { label: "Link web / WhatsApp", isMeta: false };
  }
  if (origin.channel === "direct") return { label: "Directo (escribió al número)", isMeta: false };
  return { label: origin.channel, isMeta: false };
}

function formatStarted(d: SessionDetails): string {
  const ms = d.origin?.first_seen_ms;
  let date: Date | null = typeof ms === "number" ? new Date(ms) : null;
  if (!date) {
    const first = d.messages.find((m) => typeof m.timestamp === "string" || typeof m.timestamp === "number");
    if (first) {
      date =
        typeof first.timestamp === "number"
          ? new Date(first.timestamp * 1000)
          : new Date(first.timestamp as string);
    }
  }
  if (!date || Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Cabecera "Estado actual" del inspector con datos reales de la sesión. */
export function useChatOverview(id: string | null) {
  const q = useSession(id);
  const data = useMemo<ChatOverview | null>(() => {
    if (!q.data) return null;
    const origin = formatOrigin(q.data.origin);
    return {
      sessionId: q.data.session_id,
      tag: q.data.tag,
      startedLabel: formatStarted(q.data),
      originLabel: origin.label,
      originDetail: origin.detail,
      originIsMeta: origin.isMeta,
    };
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
