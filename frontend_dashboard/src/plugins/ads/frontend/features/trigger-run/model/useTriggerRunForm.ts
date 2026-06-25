/**
 * Estado local del formulario de disparo: qué agente está elegido y el texto
 * JSON editable del textarea. Vive en un hook (no inline en la UI) para ser
 * testeable: la lógica interesante es (a) precargar el `exampleInput` del agente
 * elegido SIN pisar ediciones del usuario en cada render, y (b) derivar si el
 * texto es JSON válido para habilitar/deshabilitar el botón Run.
 *
 * FSD: el hook NO hace fetch — recibe las `agents` ya cargadas por la entity
 * (`useAgents`) desde la feature. Server-state ⇒ TanStack Query; este hook es
 * UI-state puro (anti-pattern #2: nada de server-data en useState).
 */

import { useCallback, useEffect, useState } from "react";

import type { AgentOption } from "@plugins/ads/frontend/entities/ad-analysis-run";

/** Pretty-print del example_input del agente (lo que se inyecta al textarea). */
export function formatExampleInput(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

export interface ParsedDraft {
  ok: boolean;
  value?: unknown;
  error?: string;
}

/** Parsea el texto del textarea. `""`/espacios ⇒ `{}` (entrada vacía válida). */
export function parseDraft(text: string): ParsedDraft {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, value: {} };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export interface TriggerRunForm {
  agentId: string | null;
  selectAgent: (id: string) => void;
  draft: string;
  setDraft: (text: string) => void;
  resetToExample: () => void;
  parsed: ParsedDraft;
  canRun: boolean;
}

export function useTriggerRunForm(
  agents: AgentOption[] | undefined,
): TriggerRunForm {
  const [agentId, setAgentId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");

  const findAgent = useCallback(
    (id: string | null) => agents?.find((a) => a.id === id) ?? null,
    [agents],
  );

  // Auto-seleccionar el primer agente en cuanto la lista carga (una vez): sin
  // esto el textarea arranca vacío y el usuario no sabe qué forma espera.
  useEffect(() => {
    if (agentId === null && agents && agents.length > 0) {
      setAgentId(agents[0].id);
      setDraft(formatExampleInput(agents[0].exampleInput));
    }
  }, [agents, agentId]);

  const selectAgent = useCallback(
    (id: string) => {
      setAgentId(id);
      const next = agents?.find((a) => a.id === id);
      // Cambiar de agente RE-precarga su ejemplo (descarta el draft anterior:
      // el JSON del agente A no aplica al agente B).
      setDraft(formatExampleInput(next?.exampleInput));
    },
    [agents],
  );

  const resetToExample = useCallback(() => {
    setDraft(formatExampleInput(findAgent(agentId)?.exampleInput));
  }, [findAgent, agentId]);

  const parsed = parseDraft(draft);
  const canRun = agentId !== null && parsed.ok;

  return {
    agentId,
    selectAgent,
    draft,
    setDraft,
    resetToExample,
    parsed,
    canRun,
  };
}
