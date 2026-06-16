import type { OrderDetail } from "@plugins/orders/frontend/entities/order";
import { InsBlock } from "@/shared/ui";

export function NotesPanel({ detail }: { detail: OrderDetail }) {
  // Mostramos las notas reales (human_note + cancelled_reason) si hay.
  // Si no hay, mostramos placeholder más conservador — la operación de
  // agregar notas se hace via el SchedulePanel (cuando el humano agenda).
  if (detail.notes.length === 0) {
    return (
      <InsBlock title="Notas" open={false}>
        <div
          style={{ fontSize: 11, color: "var(--fg-muted)", padding: 8 }}
        >
          Sin notas. Agendá el pedido para añadir una nota visible al equipo.
        </div>
      </InsBlock>
    );
  }
  return (
    <InsBlock title={`Notas (${detail.notes.length})`} open>
      {detail.notes.map((note, i) => (
        <div
          key={i}
          style={{
            padding: "6px 8px",
            background: "rgba(255,255,255,0.03)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--fg-soft)",
            marginBottom: 4,
            whiteSpace: "pre-wrap",
          }}
        >
          {note}
        </div>
      ))}
    </InsBlock>
  );
}
