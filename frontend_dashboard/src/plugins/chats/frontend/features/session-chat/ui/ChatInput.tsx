import { Send } from "lucide-react";

/**
 * Read-only por ahora — el dashboard observa, no envía. Cuando se habilite
 * "send-message" será un feature aparte (`features/send-message/`) que
 * reemplace este componente, no que lo extienda.
 */
export function ChatInput() {
  return (
    <div className="chat-input-area">
      <input
        type="text"
        className="chat-input-box"
        placeholder="Type a message (read-only mode)..."
        disabled
      />
      <button className="send-btn" disabled>
        <Send size={18} />
      </button>
    </div>
  );
}
