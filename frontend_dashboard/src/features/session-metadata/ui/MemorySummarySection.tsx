import { useState } from "react";
import { MemoryModal } from "@/features/memory-modal";
import type { TimelineEvent } from "../model/useCombinedHistory";
import { WorkflowTransitionTimeline } from "./WorkflowTransitionTimeline";

interface Props {
  memoryContent: string | null | undefined;
  fallbackMotivo: string | undefined;
  combinedHistory: TimelineEvent[];
}

export function MemorySummarySection({
  memoryContent,
  fallbackMotivo,
  combinedHistory,
}: Props) {
  const [open, setOpen] = useState(false);
  const content = memoryContent ?? fallbackMotivo ?? null;

  return (
    <div className="meta-section" style={{ borderBottom: "none" }}>
      <h3>AI Memory Summary</h3>
      <div style={{ marginTop: "1rem" }}>
        <button
          className="pill-btn"
          style={{
            width: "100%",
            padding: "0.75rem",
            borderRadius: "8px",
            background: "var(--agent-bubble)",
            color: "var(--accent-color)",
          }}
          onClick={() => setOpen(true)}
        >
          Visualizar resumen de conversación
        </button>
      </div>

      <MemoryModal
        open={open}
        onClose={() => setOpen(false)}
        content={content}
      />

      <WorkflowTransitionTimeline events={combinedHistory} />
    </div>
  );
}
