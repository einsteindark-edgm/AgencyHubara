import type { TimelineEvent } from "../model/useCombinedHistory";

interface Props {
  events: TimelineEvent[];
}

export function WorkflowTransitionTimeline({ events }: Props) {
  if (events.length === 0) return null;

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h3 style={{ fontSize: "0.75rem" }}>Workflow Transition Engine</h3>
      <div className="tag-history-list" style={{ marginTop: "1rem" }}>
        {events.map((ev, idx) => (
          <div
            key={idx}
            className="tag-history-item"
            style={{ alignItems: "flex-start" }}
          >
            <div
              className="tag-history-dot"
              style={{ marginTop: "6px", borderColor: "var(--accent-color)" }}
            />
            <div style={{ flex: 1 }}>
              <span
                style={{ color: "#fff", fontWeight: 500, fontSize: "0.85rem" }}
              >
                {ev.title}
              </span>
              <div
                style={{
                  fontSize: "0.75rem",
                  color: "var(--text-secondary)",
                  marginTop: "2px",
                  lineHeight: "1.3",
                }}
              >
                {ev.desc}
              </div>
              <div
                style={{
                  fontSize: "0.65rem",
                  color: "rgba(255,255,255,0.3)",
                  marginTop: "4px",
                }}
              >
                {new Date(ev.timestamp).toLocaleString()}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
