import { useState, useEffect } from 'react';
import './App.css';
import { LeftColChatList } from './components/LeftCol_ChatList';
import { CenterColChatView } from './components/CenterCol_ChatView';
import { RightColMetadata } from './components/RightCol_Metadata';

export interface ChatSession {
  session_id: string;
  phone_number: string;
  tag: string;
  motivo: string;
  active_agent_route: string;
  phone_number_id: string | null;
  last_updated_timestamp: number;
}

function App() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  useEffect(() => {
    // Initial fetch
    const fetchSessions = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/dashboard/sessions');
        const data = await res.json();
        setSessions(data.sessions || []);
      } catch (err) {
        console.error("Error fetching sessions:", err);
      }
    };
    fetchSessions();

    // Set up Server-Sent Events (SSE) for real-time updates
    const evtSource = new EventSource('http://localhost:8000/api/dashboard/stream');
    evtSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setSessions(data.sessions || []);
      } catch (e) {
        console.error("Error parsing SSE data", e);
      }
    };

    return () => evtSource.close();
  }, []);

  const selectedSession = sessions.find(s => s.session_id === selectedSessionId) || null;

  return (
    <div className="layout-container">
      {/* Left Column: Chat List */}
      <div className="col-left glass-panel">
        <LeftColChatList 
          sessions={sessions} 
          selectedSessionId={selectedSessionId}
          onSelectSession={setSelectedSessionId} 
        />
      </div>

      {/* Center Column: Chat View */}
      <div className="col-center">
        <CenterColChatView sessionId={selectedSessionId} />
      </div>

      {/* Right Column: Analytics & Metadata */}
      <div className="col-right glass-panel">
        <RightColMetadata session={selectedSession} />
      </div>
    </div>
  );
}

export default App;
