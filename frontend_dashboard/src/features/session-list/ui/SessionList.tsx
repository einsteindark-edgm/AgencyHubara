/**
 * Feature `session-list`: panel izquierdo del dashboard.
 *
 * Consume directamente `useSessions()` de la entidad (no recibe data por prop).
 * Recibe el estado de selección por prop porque la selección es cross-feature
 * (la lee también `session-chat` y `session-metadata`). El owner natural es
 * el shell de la página (`pages/Dashboard.tsx`).
 */

import type { CSSProperties } from "react";
import { useSessions } from "@/entities/session";
import { useSessionFilters } from "../model/useSessionFilters";
import { SearchBar } from "./SearchBar";
import { TagFilterBar } from "./TagFilterBar";
import { SessionListItem } from "./SessionListItem";

const emptyStateStyle: CSSProperties = {
  padding: "2rem",
  textAlign: "center",
  color: "var(--text-secondary)",
  fontSize: "0.9rem",
};

interface Props {
  selectedSessionId: string | null;
  onSelectSession: (id: string) => void;
}

export function SessionList({ selectedSessionId, onSelectSession }: Props) {
  const { data: sessions = [], isLoading, isError } = useSessions();
  const filters = useSessionFilters(sessions);

  return (
    <>
      <SearchBar value={filters.searchTerm} onChange={filters.setSearchTerm} />
      <TagFilterBar
        tags={filters.availableTags}
        active={filters.activeTag}
        onChange={filters.setActiveTag}
      />

      <div style={{ flex: 1, overflowY: "auto" }}>
        {isLoading && (
          <div style={emptyStateStyle}>Loading sessions…</div>
        )}
        {isError && (
          <div style={emptyStateStyle}>Error loading sessions</div>
        )}
        {!isLoading &&
          !isError &&
          filters.filteredSessions.map((session) => (
            <SessionListItem
              key={session.session_id}
              session={session}
              selected={selectedSessionId === session.session_id}
              onSelect={onSelectSession}
            />
          ))}
        {!isLoading && !isError && filters.filteredSessions.length === 0 && (
          <div style={emptyStateStyle}>No sessions found</div>
        )}
      </div>
    </>
  );
}
