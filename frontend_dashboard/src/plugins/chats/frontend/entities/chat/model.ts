/**
 * Entidad "chat": conversación del Agency Inbox según el prototipo Agency
 * Desktop. Es independiente de `session` (que mapea al backend SSE real); este
 * modelo cubre la bandeja completa con tag "HUMANO", presencia, etc.
 *
 * Si más adelante el backend expone estos campos, esta entidad será la única
 * que cambie (queryFn → fetch) sin tocar features.
 */

export type ChatTag = "HUMANO" | "INTERESADO" | "PENDIENTE" | "CLIENTE" | "REMARKETING" | "FRÍO";
export type AvatarColor = "purple" | "blue" | "green" | "orange" | "pink" | "teal" | "gray";
export type Presence = "online" | "away" | "off";

/**
 * Chip de pedido de la fila: a qué orden pertenece esta conversación y en qué
 * punto va el pago. `null` mientras no haya pedido registrado.
 *
 * El estado es el del PAGO, no el logístico (preparando / listo / en camino):
 * eso vive en Medusa y lo muestra el panel de pedidos del chat. La bandeja se
 * arma con el metadata del vault, sin una llamada a Medusa por fila.
 */
export interface ChatOrderBadge {
  /** Lo que se lee en el chip: "#31", o "…B3XY9Z" si no hubo display_id. */
  label: string;
  orderId: string;
  payment: "pending" | "confirmed" | "cancelled";
  /** Pedidos exitosos del cliente en esta sesión (>1 → el chip suma "+N"). */
  count: number;
}

/** Texto y tono de cada estado de pago. El texto NO es decorativo: va al
 *  `aria-label` del chip, para que el estado no viaje sólo en el color. */
export const ORDER_BADGE_META: Record<
  ChatOrderBadge["payment"],
  { label: string; tone: string }
> = {
  pending: { label: "pago por verificar", tone: "pending" },
  confirmed: { label: "pago confirmado", tone: "confirmed" },
  cancelled: { label: "pedido cancelado", tone: "cancelled" },
};

export interface ChatInboxItem {
  id: string;
  name: string;
  short: string;
  snippet: string;
  /** "HH:MM" del último movimiento, en hora Colombia. Derivado de `timestamp`. */
  time: string;
  /** Instante crudo del último movimiento (unix epoch en SEGUNDOS, tal cual lo
   *  emite el backend). Es la fuente para ordenar y para el filtro por fecha —
   *  `time` ya es texto y no sirve para comparar. */
  timestamp: number;
  /** Día calendario del último movimiento, YYYY-MM-DD **en America/Bogota**.
   *  Es lo que decide si un chat es "de hoy" y si cae dentro del rango del
   *  calendario. Vacío si el backend no mandó timestamp. */
  dayIso: string;
  /** Epoch ms del último mensaje DEL CLIENTE (null si nunca escribió). Solo
   *  avanza cuando escribe el cliente — dispara el sonido de la bandeja. */
  lastInboundMs: number | null;
  /** Pedido al que pertenece la conversación, o null. */
  order: ChatOrderBadge | null;
  tag: ChatTag;
  tagClass: string;
  color: AvatarColor;
  presence: Presence;
  unread: number;
  pinned?: boolean;
  human?: boolean;
  handoffReason?: string;
}

import type { ChatEvent } from "@plugins/chats/frontend/entities/message";

export type { ChatEvent };

export type MessageKind = "in" | "out" | "day" | "system" | "tag" | "audio";

/** Identifica de quién sale un bubble outbound (out): bot o humano operador.
 *  Sólo aplica cuando `kind === "out"`; para inbound queda undefined. */
export type OutboundAuthor = "bot" | "human";

/** Autor del mensaje citado: cliente, bot, operador, o desconocido cuando el
 *  backend no pudo resolver la cita. */
export type QuoteAuthor = "user" | "agent" | "human" | "unknown";

/** Mensaje al que el cliente respondió citándolo (reply de WhatsApp). */
export interface ChatQuote {
  author: QuoteAuthor;
  text?: string;
  /** Ya absolutizada por el adaptador, lista para `<img src>`. */
  imageUrl?: string;
}

export interface ChatMessageItem {
  kind: MessageKind;
  text?: string;
  time?: string;
  /** Día calendario YYYY-MM-DD en America/Bogota.
   *
   *  - En `kind: "day"` es el día que ANUNCIA el separador; la etiqueta visible
   *    ("Hoy" / "Ayer" / "Lunes" / "21 de agosto de 2026") se computa en render
   *    con `formatDayLabelEs`, NO acá — si se congelara en el adaptador, un
   *    chat abierto a medianoche seguiría diciendo "Hoy" al día siguiente
   *    (regla 5: los derivados de reloj se computan en render).
   *  - En una burbuja es el día al que pertenece. Vacío si no hay timestamp. */
  dayIso?: string;
  status?: "sent" | "read";
  dur?: string;
  /** Sólo definido cuando kind="out". Indica si lo escribió el bot o el humano operador. */
  author?: OutboundAuthor;
  /** URL absoluta de una imagen adjunta (comprobante de pago / foto del
   *  cliente). Cuando está presente, el bubble la renderiza. Ya viene
   *  absolutizada por el adaptador (`adaptMessage`) lista para `<img src>`. */
  imageUrl?: string;
  /** URL absoluta de un documento PDF adjunto (comprobante típico) + su
   *  nombre visible. El bubble pinta un chip clickeable que lo abre. */
  documentUrl?: string;
  documentName?: string;
  /** Presente cuando el mensaje es un reply que cita otro mensaje. */
  replyTo?: ChatQuote;
  /** Forma real del mensaje detrás del marker del historial (botones,
   *  foto con caption + visión, tap, reacción). Ausente = mensaje normal. */
  event?: ChatEvent;
}

export interface MemoryItem {
  key: string;
  value: string;
  body: string;
}

export interface RoutingLogItem {
  color: "" | "purple" | "orange";
  agent: string;
  tag: string;
  tagClass: "" | "purple" | "orange";
  time: string;
  body: string;
}

export interface NoteItem {
  author: string;
  role: string;
  time: string;
  color: AvatarColor;
  body: string;
  tags: string[];
}

export interface FileItem {
  name: string;
  size: string;
  time: string;
  kind: "pdf" | "img";
}

/** Cabecera "Estado actual" del inspector: datos REALES de la sesión (antes
 *  eran placeholders del prototipo). `originLabel` ya viene formateado
 *  ("Meta Ads · Día del Padre"); `originDetail` es el ad/headline cuando
 *  aporta algo más que el label. */
export interface ChatOverview {
  sessionId: string;
  tag: string;
  startedLabel: string;
  originLabel: string;
  originDetail?: string;
  /** true cuando el origen es un anuncio/post de Meta (link visual). */
  originIsMeta: boolean;
}
