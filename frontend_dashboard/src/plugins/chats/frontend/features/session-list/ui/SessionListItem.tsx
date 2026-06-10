import type { ChatSession } from "@plugins/chats/frontend/entities/session";
import { formatHourMinute } from "../model/format";

interface Props {
  session: ChatSession;
  selected: boolean;
  onSelect: (id: string) => void;
}

export function SessionListItem({ session, selected, onSelect }: Props) {
  return (
    <div
      className={`session-item ${selected ? "selected" : ""}`}
      onClick={() => onSelect(session.session_id)}
    >
      <div className="avatar-placeholder">{session.phone_number.slice(-2)}</div>
      <div className="session-details">
        <div className="session-top">
          <span className="session-phone">+{session.phone_number}</span>
          <span className="session-time">
            {formatHourMinute(session.last_updated_timestamp)}
          </span>
        </div>
        <div className="session-msg">
          {session.motivo || "Sin diagnóstico..."}
        </div>
        {session.tag && session.tag !== "NO_ETIQUETADO" && (
          <span className="session-tag">{session.tag}</span>
        )}
      </div>
    </div>
  );
}
