/**
 * Estado local del feature: término de búsqueda y tag activo, más la lista
 * derivada `filteredSessions`. Mantener este estado FUERA del componente
 * `SessionList` permite testearlo sin DOM.
 *
 * Reglas de filtrado (capturadas del componente legado):
 *   - search: substring case-insensitive contra `phone_number` o `motivo`.
 *   - tag: exacto.
 *   - "NO_ETIQUETADO" no aparece en la lista de tags disponibles.
 */

import { useMemo, useState } from "react";
import type { ChatSession } from "@/entities/session";

const HIDDEN_TAG = "NO_ETIQUETADO";

export interface UseSessionFiltersResult {
  searchTerm: string;
  setSearchTerm: (v: string) => void;
  activeTag: string | null;
  setActiveTag: (v: string | null) => void;
  availableTags: string[];
  filteredSessions: ChatSession[];
}

export function useSessionFilters(
  sessions: ChatSession[],
): UseSessionFiltersResult {
  const [searchTerm, setSearchTerm] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const availableTags = useMemo(() => {
    const tags = new Set<string>();
    for (const s of sessions) {
      if (s.tag && s.tag !== HIDDEN_TAG) tags.add(s.tag);
    }
    return Array.from(tags);
  }, [sessions]);

  const filteredSessions = useMemo(() => {
    const term = searchTerm.toLowerCase();
    return sessions.filter((s) => {
      const matchSearch =
        s.phone_number.includes(searchTerm) ||
        (s.motivo ?? "").toLowerCase().includes(term);
      const matchTag = activeTag ? s.tag === activeTag : true;
      return matchSearch && matchTag;
    });
  }, [sessions, searchTerm, activeTag]);

  return {
    searchTerm,
    setSearchTerm,
    activeTag,
    setActiveTag,
    availableTags,
    filteredSessions,
  };
}
