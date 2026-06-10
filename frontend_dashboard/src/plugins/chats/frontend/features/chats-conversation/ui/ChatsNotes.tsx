/**
 * Vista Notas: composer en la parte alta + lista de notas previas.
 * El draft y la lista en memoria viven en estado local — al refrescar se reseteen.
 * Si más adelante hay persistencia, esto migra a una mutation contra `entities/chat`.
 */

import { useState, useMemo } from "react";
import { useChatNotes, type NoteItem } from "@plugins/chats/frontend/entities/chat";
import { Avatar, Icon } from "@/shared/ui";

interface Props {
  chatId: string;
}

export function ChatsNotes({ chatId }: Props) {
  const { data: initial = [] } = useChatNotes(chatId);
  const [draft, setDraft] = useState("");
  const [extras, setExtras] = useState<NoteItem[]>([]);
  const notes = useMemo(() => [...extras, ...initial], [extras, initial]);

  const add = () => {
    const v = draft.trim();
    if (!v) return;
    setExtras([
      {
        author: "Tú",
        role: "Operador",
        time: "Ahora",
        color: "blue",
        body: v,
        tags: [],
      },
      ...extras,
    ]);
    setDraft("");
  };

  return (
    <div className="notes-view">
      <div className="notes-compose">
        <textarea
          placeholder="Escribe una nota interna sobre esta conversación…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
        />
        <div className="nc-foot">
          <span className="nc-hint">
            Visible solo para el equipo · Soporta @menciones
          </span>
          <button className="nc-btn" onClick={add} disabled={!draft.trim()}>
            <Icon.plus />
            Agregar nota
          </button>
        </div>
      </div>
      <div className="notes-list">
        {notes.map((n, i) => (
          <div key={i} className="note-card">
            <Avatar
              initials={n.author
                .split(" ")
                .map((x) => x[0])
                .slice(0, 2)
                .join("")}
              color={n.color}
              size={30}
            />
            <div className="nc-body">
              <div className="nc-h">
                <span className="nc-name">{n.author}</span>
                <span className="nc-role">· {n.role}</span>
                <span className="nc-time">{n.time}</span>
              </div>
              <div className="nc-text">{n.body}</div>
              {n.tags.length > 0 && (
                <div className="nc-tags">
                  {n.tags.map((t, j) => (
                    <span key={j} className="tg-chip">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
