import type { ChatMessageItem } from "@/entities/chat";
import { ChatsBubble } from "./ChatsBubble";
import { ChatsComposer } from "./ChatsComposer";
import { useAutoScroll } from "../model/useAutoScroll";

interface Props {
  messages: ChatMessageItem[];
}

export function ChatsMessageList({ messages }: Props) {
  const { containerRef, sentinelRef, showNewBadge, handleScroll, scrollToBottom } =
    useAutoScroll(messages.length);

  return (
    <div style={{ position: "relative", display: "contents" }}>
      <div className="msgs" ref={containerRef} onScroll={handleScroll}>
        {messages.map((m, i) => (
          <ChatsBubble key={i} message={m} />
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
      <ChatsComposer />
    </div>
  );
}
