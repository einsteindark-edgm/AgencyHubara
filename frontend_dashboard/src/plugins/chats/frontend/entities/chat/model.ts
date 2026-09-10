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
  tag: ChatTag;
  tagClass: string;
  color: AvatarColor;
  presence: Presence;
  unread: number;
  pinned?: boolean;
  human?: boolean;
  handoffReason?: string;
}

export type MessageKind = "in" | "out" | "day" | "system" | "tag" | "audio";

/** Identifica de quién sale un bubble outbound (out): bot o humano operador.
 *  Sólo aplica cuando `kind === "out"`; para inbound queda undefined. */
export type OutboundAuthor = "bot" | "human";

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
