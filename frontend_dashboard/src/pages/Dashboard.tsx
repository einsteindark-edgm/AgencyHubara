/**
 * Página `Dashboard`: shell de 3 columnas que ensambla las features.
 *
 * Responsabilidades de esta capa (`pages/`):
 *   - State de coordinación cross-feature (acá: `selectedSessionId`).
 *   - Montar suscripciones globales (acá: `useSessionsStream`).
 *   - Composición JSX de features.
 *
 * NO va acá:
 *   - Data fetching de dominio (vive en entities/<x>/api.ts)
 *   - UI específica de un feature (vive en features/<x>/ui/)
 *   - Estilos legacy: se cargan vía Dashboard.legacy.css y se irán
 *     reemplazando por utilidades Tailwind feature por feature.
 */

import { useState } from "react";
import "./Dashboard.legacy.css";
import { SessionList } from "@/features/session-list";
import { SessionChat } from "@/features/session-chat";
import { SessionMetadata } from "@/features/session-metadata";
import { useSessionsStream } from "@/entities/session";

export function Dashboard() {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );

  // Suscripción única al SSE — empuja la lista al cache de React Query
  // para todas las features que consuman `useSessions()`.
  useSessionsStream();

  return (
    <div className="layout-container">
      <div className="col-left glass-panel">
        <SessionList
          selectedSessionId={selectedSessionId}
          onSelectSession={setSelectedSessionId}
        />
      </div>

      <div className="col-center">
        <SessionChat sessionId={selectedSessionId} />
      </div>

      <div className="col-right glass-panel">
        <SessionMetadata sessionId={selectedSessionId} />
      </div>
    </div>
  );
}
