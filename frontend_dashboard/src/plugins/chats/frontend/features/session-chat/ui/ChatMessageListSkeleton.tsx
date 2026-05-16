export function ChatMessageListSkeleton() {
  // Uses legacy CSS classes .bubble/.bubble-user/.bubble-agent from
  // Dashboard.legacy.css. animate-pulse is built-in Tailwind v4.
  return (
    <div className="chat-messages">
      <div className="bubble bubble-user opacity-40 animate-pulse w-[45%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[60%]" />
      <div className="bubble bubble-user opacity-40 animate-pulse w-[35%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[55%]" />
    </div>
  );
}
