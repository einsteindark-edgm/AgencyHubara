import { useState, useEffect, useRef } from 'react';
import { Phone, Video, Send } from 'lucide-react';

interface Props {
  sessionId: string | null;
}

interface ChatMessage {
  ui_type: "user_message" | "agent_message" | "system_event" | "tool_execution_result" | "agent_tool_call";
  role: string;
  content: string | null;
  tool_calls?: any[];
}

interface SessionDetails {
  phone_number: string;
  messages: ChatMessage[];
}

export function CenterColChatView({ sessionId }: Props) {
  const [details, setDetails] = useState<SessionDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionId) {
      setDetails(null);
      return;
    }

    const fetchDetails = async () => {
      setLoading(true);
      try {
        const res = await fetch(`http://localhost:8000/api/dashboard/sessions/${sessionId}`);
        const data = await res.json();
        setDetails(data);
      } catch (err) {
        console.error("Failed to fetch session details", err);
      }
      setLoading(false);
    };

    fetchDetails();
    
    // Poll for updates every 3s if we have an active session
    const interval = setInterval(fetchDetails, 3000);
    return () => clearInterval(interval);
  }, [sessionId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [details]);

  if (!sessionId) {
    return (
      <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
        <p>Select a chat to start viewing</p>
      </div>
    );
  }

  if (loading && !details) {
    return <div style={{ padding: "2rem", color: "var(--text-secondary)" }}>Loading session...</div>;
  }

  return (
    <>
      <div className="chat-header">
        <div className="avatar-placeholder" style={{ width: "40px", height: "40px", fontSize: "1rem" }}>
          {details?.phone_number.slice(-2)}
        </div>
        <div className="chat-header-info" style={{ flex: 1 }}>
          <h2>+{details?.phone_number}</h2>
          <p>Online / Active Route</p>
        </div>
        <div style={{ display: "flex", gap: "1rem", color: "var(--accent-color)", cursor: "pointer" }}>
          <Phone size={20} />
          <Video size={20} />
        </div>
      </div>

      <div className="chat-messages" ref={scrollRef}>
        {(details?.messages || []).map((msg, idx) => {
          if (["system_event", "agent_tool_call", "tool_execution_result"].includes(msg.ui_type)) {
            return null; // Don't show technical events in central view
          }
          
          // Omitir inyecciones ocultas de la capa Temporal / LLM
          const isGhostTrigger = msg.ui_type === "user_message" && msg.content?.includes("[SISTEMA]:");
          const isAgentEcho = msg.ui_type === "agent_message" && (
              msg.content?.includes("ha sido etiquetada") || 
              msg.content?.includes("remarketing automático")
          );

          if (isGhostTrigger || isAgentEcho) {
              return null;
          }

          const isUser = msg.ui_type === "user_message";
          return (
            <div key={idx} className={`bubble ${isUser ? 'bubble-user' : 'bubble-agent'}`}>
              <div dangerouslySetInnerHTML={{ 
                __html: (typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content || ""))
                  .replace(/\\n/g, "<br/>")
                  .replace(/\\*\\*(.*?)\\*\\*/g, "<strong>$1</strong>") 
              }} />
            </div>
          );
        })}
      </div>

      <div className="chat-input-area">
        <input type="text" className="chat-input-box" placeholder="Type a message (read-only mode)..." disabled />
        <button className="send-btn" disabled>
          <Send size={18} />
        </button>
      </div>
    </>
  );
}
