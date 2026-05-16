/**
 * Feature `session-metadata`: panel derecho del dashboard.
 *
 * Consume:
 *   - `useSession(sessionId)` — comparte cache con `session-chat` (1 fetch para los dos).
 *   - `useSessions()` — para fallback durante el primer load del detalle (UX sin flash).
 *
 * El estado del modal de memoria vive dentro de `MemorySummarySection` —
 * `SessionMetadata` no se entera ni le importa.
 */

import { useSession, useSessions } from "@/entities/session";
import { useCombinedHistory } from "../model/useCombinedHistory";
import { CurrentStatusSection } from "./CurrentStatusSection";
import { AgentDetailsSection } from "./AgentDetailsSection";
import { MemorySummarySection } from "./MemorySummarySection";

interface Props {
  sessionId: string | null;
}

export function SessionMetadata({ sessionId }: Props) {
  const { data: details } = useSession(sessionId);
  const { data: sessions = [] } = useSessions();
  const sessionFromList = sessions.find((s) => s.session_id === sessionId);

  const combinedHistory = useCombinedHistory(details?.messages ?? []);

  if (!sessionId) {
    return (
      <div
        style={{
          display: "flex",
          height: "100%",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-secondary)",
        }}
      >
        <p>No chat selected</p>
      </div>
    );
  }

  // Fallback chain: detalle (servidor) → list snapshot (cache) → undefined.
  // Evita el flash de loading entre cambios de selección.
  const currentTag = details?.tag ?? sessionFromList?.tag;
  const currentRoute =
    details?.active_agent_route ?? sessionFromList?.active_agent_route;
  const currentPhoneId =
    details?.phone_number_id ?? sessionFromList?.phone_number_id;
  const currentMotivo = details?.motivo ?? sessionFromList?.motivo;
  const currentMemory = details?.memory_content;
  const statusHistory = details?.status_history ?? [];

  return (
    <>
      <div
        style={{
          padding: "1.5rem",
          borderBottom: "1px solid var(--panel-border)",
        }}
      >
        <h2 style={{ marginBottom: "0.25rem" }}>Analytics Sidebar</h2>
        <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
          Session details inside workflow router
        </p>
      </div>

      <CurrentStatusSection
        currentTag={currentTag}
        statusHistory={statusHistory}
      />

      <AgentDetailsSection
        activeRoute={currentRoute}
        phoneNumberId={currentPhoneId}
      />

      <MemorySummarySection
        memoryContent={currentMemory}
        fallbackMotivo={currentMotivo}
        combinedHistory={combinedHistory}
      />
    </>
  );
}
