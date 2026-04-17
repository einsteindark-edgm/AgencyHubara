import { useState, useMemo } from 'react';
import { Search } from 'lucide-react';
import type { ChatSession } from '../App';

interface Props {
  sessions: ChatSession[];
  selectedSessionId: string | null;
  onSelectSession: (id: string) => void;
}

export function LeftColChatList({ sessions, selectedSessionId, onSelectSession }: Props) {
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTag, setActiveTag] = useState<string | null>(null);

  // Derive unique tags from sessions
  const availableTags = useMemo(() => {
    const tags = new Set<string>();
    sessions.forEach(s => {
      if (s.tag && s.tag !== "NO_ETIQUETADO") tags.add(s.tag);
    });
    return Array.from(tags);
  }, [sessions]);

  // Filter sessions
  const filteredSessions = useMemo(() => {
    return sessions.filter(s => {
      const matchSearch = s.phone_number.includes(searchTerm) || (s.motivo || "").toLowerCase().includes(searchTerm.toLowerCase());
      const matchTag = activeTag ? s.tag === activeTag : true;
      return matchSearch && matchTag;
    });
  }, [sessions, searchTerm, activeTag]);

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <>
      <div className="search-bar">
        <h2 style={{ marginBottom: "1rem", fontSize: "1.2rem" }}>Chats</h2>
        <div style={{ position: "relative" }}>
          <Search size={18} style={{ position: "absolute", left: "12px", top: "10px", color: "var(--text-secondary)" }} />
          <input 
            type="text" 
            className="search-input" 
            placeholder="Search contacts, reasons..." 
            style={{ paddingLeft: "38px" }}
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
      </div>
      
      <div className="tags-filter">
        <button 
          className={`pill-btn ${activeTag === null ? 'active' : ''}`}
          onClick={() => setActiveTag(null)}
        >
          All
        </button>
        {availableTags.map(t => (
          <button 
            key={t}
            className={`pill-btn ${activeTag === t ? 'active' : ''}`}
            onClick={() => setActiveTag(t)}
          >
            {t.replace(/_/g, " ")}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {filteredSessions.map(session => (
          <div 
            key={session.session_id} 
            className={`session-item ${selectedSessionId === session.session_id ? 'selected' : ''}`}
            onClick={() => onSelectSession(session.session_id)}
          >
            <div className="avatar-placeholder">
              {session.phone_number.slice(-2)}
            </div>
            <div className="session-details">
              <div className="session-top">
                <span className="session-phone">+{session.phone_number}</span>
                <span className="session-time">{formatTime(session.last_updated_timestamp)}</span>
              </div>
              <div className="session-msg">{session.motivo || "Sin diagnóstico..."}</div>
              {session.tag && session.tag !== "NO_ETIQUETADO" && (
                <span className="session-tag">{session.tag}</span>
              )}
            </div>
          </div>
        ))}
        {filteredSessions.length === 0 && (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--text-secondary)", fontSize: "0.9rem" }}>
            No sessions found
          </div>
        )}
      </div>
    </>
  );
}
