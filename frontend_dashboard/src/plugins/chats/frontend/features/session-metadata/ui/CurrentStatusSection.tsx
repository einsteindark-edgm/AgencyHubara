import type { StatusHistoryEntry } from "@/entities/session";

interface Props {
  currentTag: string | undefined;
  /** Ya viene en orden cronológico ascendente del backend; lo invertimos para mostrarlo. */
  statusHistory: StatusHistoryEntry[];
}

export function CurrentStatusSection({ currentTag, statusHistory }: Props) {
  const reversed = [...statusHistory].reverse();

  return (
    <div className="meta-section">
      <h3>Current Status</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        {currentTag && currentTag !== "NO_ETIQUETADO" ? (
          <span
            className="session-tag"
            style={{
              fontSize: "0.8rem",
              padding: "4px 10px",
              borderRadius: "12px",
            }}
          >
            TAG ACTUAL: {currentTag}
          </span>
        ) : (
          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
            Ninguna etiqueta asignada
          </span>
        )}
      </div>

      {reversed.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <h4
            style={{
              fontSize: "0.75rem",
              color: "var(--text-secondary)",
              marginBottom: "0.5rem",
              textTransform: "none",
            }}
          >
            Historical Tags Profile
          </h4>
          <div className="tag-history-list" style={{ maxHeight: "250px" }}>
            {reversed.map((s, idx) => (
              <div
                key={idx}
                className="tag-history-item"
                style={{ alignItems: "flex-start" }}
              >
                <div
                  className="tag-history-dot"
                  style={{ marginTop: "6px", borderColor: "#10b981" }}
                />
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      color: "#10b981",
                      fontWeight: 500,
                      fontSize: "0.85rem",
                    }}
                  >
                    {s.tag || "NO_ETIQUETADO"}
                  </span>
                  <div
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--text-secondary)",
                      marginTop: "2px",
                      lineHeight: "1.3",
                    }}
                  >
                    <strong>Agente Enrutado:</strong> {s.active_route} <br />
                    <strong>Motivo:</strong> {s.motivo}
                  </div>
                  <div
                    style={{
                      fontSize: "0.65rem",
                      color: "rgba(255,255,255,0.3)",
                      marginTop: "4px",
                    }}
                  >
                    {new Date(s.timestamp * 1000).toLocaleString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
