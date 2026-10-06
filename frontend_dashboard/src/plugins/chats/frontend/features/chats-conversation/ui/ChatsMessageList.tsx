import { Fragment, useMemo, useState } from "react";

import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";
import { ChatsBubble } from "./ChatsBubble";
import { ChatsComposer } from "./ChatsComposer";
import { TurnThreadModal } from "./TurnThreadModal";
import { useAutoScroll } from "../model/useAutoScroll";

/** Un día de conversación: su separador (si lo hay) y los mensajes que le
 *  siguen. `startIndex` conserva la posición original de cada mensaje en la
 *  lista plana para que la key no cambie al agregarse un día nuevo. */
interface DayGroup {
  key: string;
  startIndex: number;
  items: ChatMessageItem[];
}

/**
 * Parte la lista plana en un bloque por día.
 *
 * El separador es `position: sticky`, y varios sticky HERMANOS dentro del mismo
 * contenedor se pegan todos al tope a la vez y se solapan (se vio en pantalla:
 * "1 DE SI DOMINGO E 2026"). Con un bloque por día, cada separador sólo puede
 * pegarse dentro de SU bloque y lo empuja el del día siguiente — que es el
 * comportamiento de WhatsApp.
 *
 * Los mensajes anteriores al primer separador (historial legacy sin timestamp)
 * arrancan un bloque sin encabezado en vez de perderse.
 */
function groupByDay(messages: ChatMessageItem[]): DayGroup[] {
  const groups: DayGroup[] = [];
  messages.forEach((m, i) => {
    if (m.kind === "day" || groups.length === 0) {
      groups.push({ key: m.dayIso ?? `grupo-${i}`, startIndex: i, items: [] });
    }
    groups[groups.length - 1].items.push(m);
  });
  return groups;
}

interface Props {
  messages: ChatMessageItem[];
  /** Necesario para que el composer cablee mutaciones del handoff. Si es null,
   *  el composer no se monta. */
  chatId: string | null;
}

/** Índice del ÚLTIMO mensaje de cada turno del bot: ahí va su botón. */
function lastOfTurn(messages: ChatMessageItem[]): Set<number> {
  const last = new Map<string, number>();
  messages.forEach((m, i) => {
    if (m.turnKey) last.set(m.turnKey, i);
  });
  return new Set(last.values());
}

export function ChatsMessageList({ messages, chatId }: Props) {
  const { containerRef, sentinelRef, showNewBadge, handleScroll, scrollToBottom } =
    useAutoScroll(messages.length);
  const turnEnds = useMemo(() => lastOfTurn(messages), [messages]);
  const [openTurn, setOpenTurn] = useState<string | null>(null);

  return (
    <div style={{ position: "relative", display: "contents" }}>
      <div className="msgs" ref={containerRef} onScroll={handleScroll}>
        {groupByDay(messages).map((group) => (
          <div className="day-group" key={group.key}>
            {group.items.map((m, i) => {
              const index = group.startIndex + i;
              const turnKey = m.turnKey;
              return (
                <Fragment key={index}>
                  <ChatsBubble message={m} />
                  {/* Hilo del turno (plan del laboratorio PR 17): visible
                      siempre, también en la app Android (sin hover). */}
                  {turnKey && chatId && turnEnds.has(index) ? (
                    <button
                      type="button"
                      onClick={() => setOpenTurn(turnKey)}
                      aria-label="Ver el hilo del turno"
                      className={
                        "my-0.5 w-fit rounded-md border border-line bg-transparent px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
                        (m.kind === "in" ? "self-start" : "self-end")
                      }
                    >
                      Hilo del turno
                    </button>
                  ) : null}
                </Fragment>
              );
            })}
          </div>
        ))}
        <div ref={sentinelRef} />
      </div>
      {showNewBadge && (
        <button
          className="new-msg-badge"
          style={{
            position: "absolute",
            bottom: "56px",
            right: "1rem",
            background: "var(--color-accent-soft)",
            color: "var(--color-accent-fg)",
            border: "1px solid var(--color-accent-soft)",
            borderRadius: "9999px",
            padding: "4px 12px",
            fontSize: 13,
            cursor: "pointer",
            zIndex: 10,
          }}
          onClick={scrollToBottom}
        >
          ↓ Nuevo mensaje
        </button>
      )}
      <ChatsComposer chatId={chatId} />
      {openTurn && chatId ? (
        <TurnThreadModal sid={chatId} turnKey={openTurn} onClose={() => setOpenTurn(null)} />
      ) : null}
    </div>
  );
}
