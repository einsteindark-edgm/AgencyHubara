/**
 * Deriva el "Workflow Transition Engine" timeline a partir de los mensajes
 * de la sesión. Hoy sólo agrega `tool_execution_result`s, pero la idea es
 * que aquí se centralice la merge de eventos cuando se sumen más fuentes
 * (signals de Temporal, status_change events, etc.).
 *
 * Mantener esta lógica en un hook separado nos permite:
 *   - testearla sin DOM
 *   - reutilizarla si una futura "Session timeline page" la necesita
 */

import { useMemo } from "react";
import type { ChatMessage } from "@/entities/message";

export interface TimelineEvent {
  type: ChatMessage["ui_type"];
  timestamp: number;
  title: string;
  desc: string;
}

export function useCombinedHistory(messages: ChatMessage[]): TimelineEvent[] {
  return useMemo(() => {
    const events: TimelineEvent[] = [];

    for (const m of messages) {
      if (m.ui_type !== "tool_execution_result") continue;

      const ts =
        typeof m.timestamp === "number"
          ? m.timestamp
          : typeof m.timestamp === "string"
            ? new Date(m.timestamp).getTime()
            : Date.now();

      events.push({
        type: m.ui_type,
        timestamp: ts,
        title: `Tool Result: ${m.name ?? "System"}`,
        desc: m.content ?? "",
      });
    }

    return events.sort((a, b) => b.timestamp - a.timestamp);
  }, [messages]);
}
