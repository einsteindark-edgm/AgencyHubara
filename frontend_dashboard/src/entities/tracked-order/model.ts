/**
 * Entidad "tracked-order": pedido que el ETA agent está siguiendo (desde
 * `preparing` en adelante). Cada cambio de stage produce un `TrackedEvent`
 * con el mensaje que el agente envió y la respuesta del cliente.
 */

export type TrackedStage = "preparing" | "ready" | "shipping" | "out" | "delivered";

export interface TrackedStageMeta {
  key: TrackedStage;
  label: string;
  color: string;
}

export const TRACKED_STAGES: TrackedStageMeta[] = [
  { key: "preparing", label: "En preparación",   color: "#ffb44a" },
  { key: "ready",     label: "Lista para envío", color: "#d68aff" },
  { key: "shipping",  label: "En camino",        color: "#5fdcff" },
  { key: "out",       label: "En reparto",       color: "#5fa9ff" },
  { key: "delivered", label: "Entregada",        color: "#5be07b" },
];

export interface TrackedEvent {
  stage: TrackedStage;
  time: string;
  date: string;
  note?: string;
  agentMsg: string;
  reply?: string | null;
  flagged?: boolean;
  flag?: string;
}

export interface TrackedOrder {
  id: string;
  customer: string;
  short: string;
  color: "a" | "b" | "c" | "d" | "e" | "f";
  city: string;
  current: TrackedStage;
  channel: string;
  needs: boolean;
  payType: "cod" | "confirmed";
  total: number;
  messagesUnread: number;
  events: TrackedEvent[];
}
