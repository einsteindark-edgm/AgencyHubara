import { UserCircle } from "lucide-react";

interface Props {
  activeRoute: string | undefined;
  phoneNumberId: string | null | undefined;
}

export function AgentDetailsSection({ activeRoute, phoneNumberId }: Props) {
  return (
    <div className="meta-section">
      <h3>Agent Details</h3>
      <div className="agent-profile">
        <div className="agent-avatar">
          <UserCircle size={24} color="#fff" />
        </div>
        <div className="agent-info">
          <div
            style={{ fontWeight: 600, color: "#fff", fontSize: "0.95rem" }}
          >
            {activeRoute}
          </div>
          <p>Active routing handler</p>
        </div>
      </div>
      <div
        style={{
          marginTop: "1rem",
          fontSize: "0.85rem",
          color: "var(--text-secondary)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginBottom: "0.25rem",
          }}
        >
          <span>Platform Info:</span>
          <span style={{ color: "#fff" }}>
            {phoneNumberId ? "WhatsApp API" : "Simulated/Web"}
          </span>
        </div>
      </div>
    </div>
  );
}
