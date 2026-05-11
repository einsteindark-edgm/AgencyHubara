import { useEffect, useRef } from "react";
import { type ChatMessage, isVisibleChatMessage } from "@/entities/message";
import { ChatBubble } from "./ChatBubble";

interface Props {
  messages: ChatMessage[];
}

export function ChatMessageList({ messages }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll al fondo cuando llega un mensaje nuevo. Comparamos por length
  // para evitar scroll si TanStack Query refetcha el mismo array.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length]);

  const visible = messages.filter(isVisibleChatMessage);

  return (
    <div className="chat-messages" ref={scrollRef}>
      {visible.map((msg, idx) => (
        <ChatBubble key={idx} message={msg} />
      ))}
    </div>
  );
}
