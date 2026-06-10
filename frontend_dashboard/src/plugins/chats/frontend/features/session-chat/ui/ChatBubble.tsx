import type { ChatMessage, MessageSender } from "@plugins/chats/frontend/entities/message";

interface Props {
  message: ChatMessage;
  sender: MessageSender;
}

/**
 * Render del contenido del agente con micro-markdown:
 *   - `\n` literal → <br/>  (los logs JSON de exoclaw vienen escapados)
 *   - `**texto**` → <strong>texto</strong>
 *
 * `dangerouslySetInnerHTML` está acotado a contenido producido por el agente
 * o el cliente vía WhatsApp; no es input arbitrario de la web. Si en el futuro
 * llega contenido HTML del usuario, sanitizar con DOMPurify acá.
 */
function renderAgentMarkup(content: string | null): string {
  const text = typeof content === "string" ? content : "";
  return text
    .replace(/\\n/g, "<br/>")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
}

export function ChatBubble({ message, sender }: Props) {
  const isUser = sender === "user";
  const html = renderAgentMarkup(
    typeof message.content === "string"
      ? message.content
      : JSON.stringify(message.content ?? ""),
  );

  return (
    <div className={`bubble ${isUser ? "bubble-user" : "bubble-agent"}`}>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
