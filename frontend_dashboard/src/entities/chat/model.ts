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
  time: string;
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

export interface ChatMessageItem {
  kind: MessageKind;
  text?: string;
  time?: string;
  status?: "sent" | "read";
  dur?: string;
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
