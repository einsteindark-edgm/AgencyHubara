import { UserCircle } from 'lucide-react';
import type { ChatSession } from '../App';
import { useEffect, useState, useMemo } from 'react';

interface Props {
  session: ChatSession | null;
}

export function RightColMetadata({ session }: Props) {
  const [details, setDetails] = useState<any>(null);
  const [isMemoryModalOpen, setIsMemoryModalOpen] = useState(false);

  // We need to fetch the session specific details to get status_history etc.
  useEffect(() => {
    if (!session?.session_id) {
       setDetails(null);
       return;
    }

    const fetchDetails = () => {
      fetch(`http://localhost:8000/api/dashboard/sessions/${session.session_id}`)
        .then(res => res.json())
        .then(data => setDetails(data))
        .catch(err => console.error(err));
    };

    fetchDetails(); // Initial fetch
    
    // Poll for updates every 3s to stay synchronized with chat
    const interval = setInterval(fetchDetails, 3000);
    return () => clearInterval(interval);
  }, [session?.session_id]);

  const statusHistory = [...(details?.status_history || [])].reverse();

   const combinedHistory = useMemo(() => {
      let events: any[] = [];

      const msgs = details?.messages || [];
      msgs.forEach((m: any) => {
         if (m.ui_type === "tool_execution_result") {
            let ts = m.timestamp ? new Date(m.timestamp).getTime() : Date.now();
            let title = `Tool Result: ${m.name || "System"}`;
            let desc = m.content || "";

            events.push({
               type: m.ui_type,
               timestamp: ts,
               title,
               desc
            });
         }
      });

      events.sort((a, b) => b.timestamp - a.timestamp);
      return events;
   }, [details]);

  if (!session) {
    return (
      <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
        <p>No chat selected</p>
      </div>
    );
  }

  const currentTag = details?.tag || session.tag;
  const currentMotivo = details?.motivo || session.motivo;
  const currentMemory = details?.memory_content;
  const currentRoute = details?.active_agent_route || session.active_agent_route;
  const currentPhoneId = details?.phone_number_id || session.phone_number_id;

  return (
    <>
      <div style={{ padding: "1.5rem", borderBottom: "1px solid var(--panel-border)" }}>
         <h2 style={{ marginBottom: "0.25rem" }}>Analytics Sidebar</h2>
         <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>Session details inside workflow router</p>
      </div>
      
      <div className="meta-section">
        <h3>Current Status</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {currentTag && currentTag !== "NO_ETIQUETADO" ? (
             <span className="session-tag" style={{ fontSize: "0.8rem", padding: "4px 10px", borderRadius: "12px" }}>
               TAG ACTUAL: {currentTag}
             </span>
          ) : (
            <span style={{ fontSize: '0.8rem', color: "var(--text-secondary)" }}>Ninguna etiqueta asignada</span>
          )}
        </div>
        
        {statusHistory.length > 0 && (
          <div style={{ marginTop: "1rem" }}>
            <h4 style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: "0.5rem", textTransform: "none" }}>Historical Tags Profile</h4>
            <div className="tag-history-list" style={{ maxHeight: "250px" }}>
              {statusHistory.map((s: any, idx: number) => (
                <div key={idx} className="tag-history-item" style={{ alignItems: "flex-start" }}>
                  <div className="tag-history-dot" style={{ marginTop: "6px", borderColor: "#10b981" }}></div>
                  <div style={{ flex: 1 }}>
                     <span style={{ color: "#10b981", fontWeight: 500, fontSize: "0.85rem" }}>{s.tag || 'NO_ETIQUETADO'}</span>
                     <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: "2px", lineHeight: "1.3" }}>
                       <strong>Agente Enrutado:</strong> {s.active_route} <br/> 
                       <strong>Motivo:</strong> {s.motivo}
                     </div>
                     <div style={{ fontSize: "0.65rem", color: "rgba(255,255,255,0.3)", marginTop: "4px" }}>
                       {new Date(s.timestamp * 1000).toLocaleString()}
                     </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="meta-section">
        <h3>Agent Details</h3>
        <div className="agent-profile">
          <div className="agent-avatar">
            <UserCircle size={24} color="#fff" />
          </div>
          <div className="agent-info">
             <div style={{ fontWeight: 600, color: "#fff", fontSize: "0.95rem" }}>{currentRoute}</div>
             <p>Active routing handler</p>
          </div>
        </div>
        <div style={{ marginTop: "1rem", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
            <span>Platform Info:</span>
            <span style={{ color: "#fff" }}>{currentPhoneId ? "WhatsApp API" : "Simulated/Web"}</span>
          </div>
        </div>
      </div>

      <div className="meta-section" style={{ borderBottom: "none" }}>
        <h3>AI Memory Summary</h3>
        <div style={{ marginTop: "1rem" }}>
           <button 
             className="pill-btn" 
             style={{ width: "100%", padding: "0.75rem", borderRadius: "8px", background: "var(--agent-bubble)", color: "var(--accent-color)" }}
             onClick={() => setIsMemoryModalOpen(true)}
           >
             Visualizar resumen de conversación
           </button>
        </div>
        
        {/* Modal */}
        {isMemoryModalOpen && (
          <div className="memory-modal-overlay" onClick={() => setIsMemoryModalOpen(false)}>
            <div className="memory-modal-content" onClick={e => e.stopPropagation()}>
               <div className="memory-modal-header">
                 <h2>Memory Summary</h2>
                 <button onClick={() => setIsMemoryModalOpen(false)} style={{ background: "transparent", border: "none", color: "#fff", cursor: "pointer", fontSize: "1.5rem", padding: "0 0.5rem" }}>&times;</button>
               </div>
               <div className="memory-modal-body">
                 <p style={{ whiteSpace: "pre-wrap", lineHeight: "1.6", color: "var(--text-primary)" }}>
                   {currentMemory || currentMotivo || "No hay resumen de memoria disponible todavía."}
                 </p>
               </div>
            </div>
          </div>
        )}
        
        {combinedHistory.length > 0 && (
          <div style={{ marginTop: "1.5rem" }}>
             <h3 style={{ fontSize: "0.75rem" }}>Workflow Transition Engine</h3>
             <div className="tag-history-list" style={{ marginTop: "1rem" }}>
               {combinedHistory.map((ev: any, idx: number) => (
                 <div key={idx} className="tag-history-item" style={{ alignItems: "flex-start" }}>
                    <div className="tag-history-dot" style={{ marginTop: "6px", borderColor: ev.type === 'status_change' ? '#10b981' : 'var(--accent-color)' }}></div>
                    <div style={{ flex: 1 }}>
                       <span style={{ color: ev.type === 'status_change' ? '#10b981' : '#fff', fontWeight: 500, fontSize: "0.85rem" }}>{ev.title}</span>
                       <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: "2px", lineHeight: "1.3" }}>
                         {ev.desc}
                       </div>
                       <div style={{ fontSize: "0.65rem", color: "rgba(255,255,255,0.3)", marginTop: "4px" }}>
                         {new Date(ev.timestamp).toLocaleString()}
                       </div>
                    </div>
                 </div>
               ))}
             </div>
          </div>
        )}
      </div>
    </>
  );
}
