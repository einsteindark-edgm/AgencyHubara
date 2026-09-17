import type {
  ChatEvent,
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

/**
 * Etiqueta de tipo sobre la burbuja ("Bot · Botones de respuesta"). Dice de un
 * vistazo QUÉ fue el mensaje, que es justo lo que el marker de texto plano
 * obligaba a deducir leyendo.
 */
function Labeled({
  side,
  label,
  children,
}: {
  side: "in" | "out";
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`wa-msg wa-msg-${side}`}>
      <span className="wa-label">
        <span className="dot" />
        {label}
      </span>
      {children}
    </div>
  );
}

/** El mensaje con botones, reconstruido: el cuerpo que leyó el cliente y los
 *  botones como botones. El que tocó queda marcado ACÁ, dentro del mensaje
 *  original — que es donde el operador lo busca. */
function ButtonsMessage({
  event,
  time,
}: {
  event: Extract<ChatEvent, { kind: "bot_buttons" }>;
  time?: string;
}) {
  return (
    <Labeled side="out" label="Bot · Botones de respuesta">
      <div className="bubble b-out wa-buttons">
        {event.body && <div className="wa-buttons-body">{event.body}</div>}
        <div className="wa-buttons-list">
          {event.buttons.map((b) => (
            <span
              key={b.title}
              data-testid="wa-button"
              className={"wa-button" + (b.touched ? " on" : "")}
              aria-label={
                b.touched ? `${b.title}, el cliente tocó este botón` : undefined
              }
            >
              {b.title}
              {b.touched && <span className="wa-button-badge">Tocado</span>}
            </span>
          ))}
        </div>
        <div className="meta">{time}</div>
      </div>
    </Labeled>
  );
}

/** Un tap no es un mensaje que el cliente escribió: es un clic. Se resume en
 *  una línea en vez de ocupar una burbuja igual que el texto real. */
function TapChip({ title, time }: { title: string; time?: string }) {
  return (
    <div className="wa-chip wa-chip-tap">
      <span className="wa-chip-ico">
        <Icon.check />
      </span>
      <span className="wa-chip-body">
        Tocó <b>{title}</b>
      </span>
      {time && <span className="wa-chip-time">{time}</span>}
    </div>
  );
}

/** Reacción: el emoji ES el mensaje. Sin emoji (historial viejo, donde la
 *  traducción lo tiraba) se dice que reaccionó y nada más — no se inventa. */
function ReactionChip({
  event,
  time,
}: {
  event: Extract<ChatEvent, { kind: "reaction" }>;
  time?: string;
}) {
  return (
    <div className="wa-chip wa-chip-reaction">
      {event.emoji && (
        <span className="wa-chip-emoji" role="img" aria-label="Reacción">
          {event.emoji}
        </span>
      )}
      <span className="wa-chip-body">
        {event.author === "bot" ? "El bot reaccionó" : "El cliente reaccionó"}
      </span>
      {time && <span className="wa-chip-time">{time}</span>}
    </div>
  );
}

/** Lo que describió la IA sobre la foto, separado de lo que escribió la
 *  persona: de un vistazo se distingue qué es humano y qué es máquina. */
function VisionBlock({ text }: { text: string }) {
  return (
    <div className="wa-vision" aria-label="Descripción de la IA">
      <span className="wa-vision-tag">
        <Icon.spark />
        Visión IA
      </span>
      <span className="wa-vision-text">{text}</span>
    </div>
  );
}

export function ChatsBubble({ message: m }: Props) {
  // La etiqueta del separador se computa ACÁ, en render, a partir del `dayIso`
  // que trae el adaptador (regla 5: derivados de reloj nunca en el mapper).
  // Congelar "Hoy" en la cache haría que un chat abierto de noche siguiera
  // diciendo "Hoy" pasada la medianoche.
  if (m.kind === "day")
    return <div className="day">{m.dayIso ? formatDayLabelEs(m.dayIso) : m.text}</div>;
  // Eventos con forma propia: el marker de texto plano no los representaba.
  if (m.event?.kind === "button_tap")
    return <TapChip title={m.event.title} time={m.time} />;
  if (m.event?.kind === "reaction")
    return <ReactionChip event={m.event} time={m.time} />;
  if (m.event?.kind === "bot_buttons")
    return <ButtonsMessage event={m.event} time={m.time} />;
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
  const photo = m.event?.kind === "customer_photo" ? m.event : undefined;
  const bubbleClass =
    "bubble " +
    (m.kind === "out" ? "b-out" : "b-in") +
    (isHuman ? " b-human" : "") +
    (photo ? " wa-photo" : "");
  const bubble = (
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
      {photo ? (
        <>
          {photo.caption && <div className="wa-caption">{photo.caption}</div>}
          {photo.vision && <VisionBlock text={photo.vision} />}
        </>
      ) : (
        m.text
      )}
      <div className="meta">
        {m.time}
        {m.kind === "out" && m.status === "read" && <Icon.check />}
      </div>
    </div>
  );

  if (!photo) return bubble;
  return (
    <Labeled
      side="in"
      label={
        photo.receipt
          ? "Cliente · Comprobante"
          : photo.caption
            ? "Cliente · Imagen con texto"
            : "Cliente · Imagen"
      }
    >
      {bubble}
    </Labeled>
  );
}
