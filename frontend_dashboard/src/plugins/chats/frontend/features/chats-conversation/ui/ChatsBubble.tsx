import type {
  ChatMessageItem,
  ChatQuote,
} from "@plugins/chats/frontend/entities/chat";
import { Icon } from "@/shared/ui";
import { formatDayLabelEs } from "@/shared/lib";

interface Props {
  message: ChatMessageItem;
}

const QUOTE_AUTHOR_LABEL: Record<ChatQuote["author"], string> = {
  user: "Cliente",
  agent: "Bot",
  human: "Humano",
  unknown: "Mensaje anterior",
};

/** Mensaje citado (reply de WhatsApp) encima del contenido de la burbuja. */
function QuoteBlock({ quote }: { quote: ChatQuote }) {
  const hasContent = Boolean(quote.text || quote.imageUrl);
  return (
    <figure
      className={`bubble-quote q-${quote.author}`}
      aria-label="Respondiendo a"
    >
      {quote.imageUrl && (
        <a
          className="bubble-quote-img"
          href={quote.imageUrl}
          target="_blank"
          rel="noopener noreferrer"
          title="Abrir imagen citada"
        >
          <img src={quote.imageUrl} alt="Imagen citada" loading="lazy" />
        </a>
      )}
      <figcaption className="bubble-quote-body">
        <span className="bubble-quote-author">
          {QUOTE_AUTHOR_LABEL[quote.author]}
        </span>
        <span className="bubble-quote-text">
          {hasContent ? (quote.text ?? "📷 Foto") : "Mensaje no disponible"}
        </span>
      </figcaption>
    </figure>
  );
}

export function ChatsBubble({ message: m }: Props) {
  // La etiqueta del separador se computa ACÁ, en render, a partir del `dayIso`
  // que trae el adaptador (regla 5: derivados de reloj nunca en el mapper).
  // Congelar "Hoy" en la cache haría que un chat abierto de noche siguiera
  // diciendo "Hoy" pasada la medianoche.
  if (m.kind === "day")
    return <div className="day">{m.dayIso ? formatDayLabelEs(m.dayIso) : m.text}</div>;
  if (m.kind === "system")
    return (
      <div className="system">
        <span className="dot" />
        {m.text}
      </div>
    );
  if (m.kind === "tag")
    return (
      <div className="system tag-change">
        <span className="dot" />
        {m.text}
      </div>
    );
  if (m.kind === "audio") {
    return (
      <div className="bubble b-out audio">
        <span className="play"><Icon.mic /></span>
        <span className="wave">
          {Array.from({ length: 32 }).map((_, i) => (
            <i
              key={i}
              style={{
                height: 4 + Math.abs(Math.sin(i * 1.7)) * 14 + "px",
              }}
            />
          ))}
        </span>
        <span className="dur">{m.dur}</span>
        <span className="meta" style={{ marginLeft: 6 }}>
          {m.time}
        </span>
      </div>
    );
  }
  const isHuman = m.kind === "out" && m.author === "human";
  const bubbleClass =
    "bubble " +
    (m.kind === "out" ? "b-out" : "b-in") +
    (isHuman ? " b-human" : "");
  return (
    <div className={bubbleClass}>
      {isHuman && (
        <div className="b-human-tag">
          <Icon.user />
          <span>Humano</span>
        </div>
      )}
      {m.replyTo && <QuoteBlock quote={m.replyTo} />}
      {m.imageUrl && (
        <a
          className="bubble-img"
          href={m.imageUrl}
          target="_blank"
          rel="noopener noreferrer"
          title="Abrir imagen en tamaño completo"
        >
          <img
            src={m.imageUrl}
            alt={
              m.kind === "out"
                ? "Imagen enviada al cliente"
                : "Imagen enviada por el cliente"
            }
            loading="lazy"
          />
        </a>
      )}
      {m.documentUrl && (
        <a
          className="bubble-doc"
          href={m.documentUrl}
          target="_blank"
          rel="noopener noreferrer"
          title="Abrir el documento"
        >
          <Icon.doc />
          <span className="bubble-doc-name">
            {m.documentName ?? "Documento PDF"}
          </span>
        </a>
      )}
      {m.text}
      <div className="meta">
        {m.time}
        {m.kind === "out" && m.status === "read" && <Icon.check />}
      </div>
    </div>
  );
}
