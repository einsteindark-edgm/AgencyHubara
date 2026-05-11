/**
 * Modal genérico de "memory summary". Recibe el contenido por prop y delega
 * el control de apertura al caller. NO sabe nada de sesiones — eso lo
 * orquesta `session-metadata` (o cualquier futura feature).
 */

interface Props {
  open: boolean;
  onClose: () => void;
  content: string | null;
  fallback?: string;
  title?: string;
}

const DEFAULT_FALLBACK = "No hay resumen de memoria disponible todavía.";

export function MemoryModal({
  open,
  onClose,
  content,
  fallback = DEFAULT_FALLBACK,
  title = "Memory Summary",
}: Props) {
  if (!open) return null;

  return (
    <div className="memory-modal-overlay" onClick={onClose}>
      <div
        className="memory-modal-content"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="memory-modal-header">
          <h2>{title}</h2>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "#fff",
              cursor: "pointer",
              fontSize: "1.5rem",
              padding: "0 0.5rem",
            }}
            aria-label="Close memory modal"
          >
            &times;
          </button>
        </div>
        <div className="memory-modal-body">
          <p
            style={{
              whiteSpace: "pre-wrap",
              lineHeight: "1.6",
              color: "var(--text-primary)",
            }}
          >
            {content || fallback}
          </p>
        </div>
      </div>
    </div>
  );
}
