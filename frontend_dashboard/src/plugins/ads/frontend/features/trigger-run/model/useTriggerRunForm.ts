/**
 * Estado local del formulario de disparo: qué agente está elegido y el texto
 * JSON editable del textarea. Vive en un hook (no inline en la UI) para ser
 * testeable: la lógica interesante es (a) precargar la ENTRADA REAL de Meta
 * (`live`, del endpoint analysis-input) apenas está disponible — el JSON de
 * ejemplo es solo el fallback sin conexión —, (b) nunca pisar una edición
 * manual del usuario, y (c) derivar si el texto es JSON válido para
 * habilitar/deshabilitar el botón Run.
 *
 * FSD: el hook NO hace fetch — recibe las `agents` y el `live` ya cargados por
 * las entities (`useAgents` / `useMetaAnalysisInput`) desde la feature.
 * Server-state ⇒ TanStack Query; este hook es UI-state puro (anti-pattern #2:
 * nada de server-data en useState).
 */

import { useCallback, useEffect, useState } from "react";

import type { AgentOption } from "@plugins/ads/frontend/entities/ad-analysis-run";

/** Pretty-print del JSON que se inyecta al textarea (ejemplo o datos reales). */
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

/** De dónde salió el draft actual — la UI lo etiqueta honesto. */
export type DraftSource = "example" | "meta" | "edited";

export interface TriggerRunForm {
  agentId: string | null;
  selectAgent: (id: string) => void;
  draft: string;
  setDraft: (text: string) => void;
  resetToExample: () => void;
  /** Vuelve a los datos reales de Meta (no-op si `live` no está disponible). */
  resetToLive: () => void;
  source: DraftSource;
  parsed: ParsedDraft;
  canRun: boolean;
}

export function useTriggerRunForm(
  agents: AgentOption[] | undefined,
  live?: unknown,
): TriggerRunForm {
  const [agentId, setAgentId] = useState<string | null>(null);
  const [draft, setDraftState] = useState<string>("");
  const [source, setSource] = useState<DraftSource>("example");

  const findAgent = useCallback(
    (id: string | null) => agents?.find((a) => a.id === id) ?? null,
    [agents],
  );

  // Auto-seleccionar el primer agente en cuanto la lista carga (una vez). La
  // precarga prefiere los datos REALES de Meta si ya llegaron; si no, el ejemplo
  // (y el efecto de abajo lo reemplaza cuando `live` aparece).
  useEffect(() => {
    if (agentId === null && agents && agents.length > 0) {
      setAgentId(agents[0].id);
      if (live != null) {
        setDraftState(formatExampleInput(live));
        setSource("meta");
      } else {
        setDraftState(formatExampleInput(agents[0].exampleInput));
      }
    }
  }, [agents, agentId, live]);

  // Los datos reales llegan (o se refrescan) DESPUÉS de precargar el ejemplo →
  // reemplazan el draft, salvo que el usuario ya lo haya editado a mano.
  useEffect(() => {
    if (live != null && source !== "edited") {
      setDraftState(formatExampleInput(live));
      setSource("meta");
    }
  }, [live, source]);

  const setDraft = useCallback((text: string) => {
    setDraftState(text);
    setSource("edited");
  }, []);

  const selectAgent = useCallback(
    (id: string) => {
      setAgentId(id);
      // Cambiar de agente RE-precarga su entrada (descarta el draft anterior:
      // el JSON del agente A no aplica al agente B). Reales > ejemplo.
      const next = agents?.find((a) => a.id === id);
      if (live != null) {
        setDraftState(formatExampleInput(live));
        setSource("meta");
      } else {
        setDraftState(formatExampleInput(next?.exampleInput));
        setSource("example");
      }
    },
    [agents, live],
  );

  const resetToExample = useCallback(() => {
    setDraftState(formatExampleInput(findAgent(agentId)?.exampleInput));
    setSource("example");
  }, [findAgent, agentId]);

  const resetToLive = useCallback(() => {
    if (live == null) return;
    setDraftState(formatExampleInput(live));
    setSource("meta");
  }, [live]);

  const parsed = parseDraft(draft);
  const canRun = agentId !== null && parsed.ok;

  return {
    agentId,
    selectAgent,
    draft,
    setDraft,
    resetToExample,
    resetToLive,
    source,
    parsed,
    canRun,
  };
}
