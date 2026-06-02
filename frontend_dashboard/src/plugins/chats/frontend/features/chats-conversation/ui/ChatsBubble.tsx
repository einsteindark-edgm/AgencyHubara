import type { ChatMessageItem } from "@/entities/chat";
import { Icon } from "@/shared/ui";

interface Props {
  message: ChatMessageItem;
}

export function ChatsBubble({ message: m }: Props) {
  if (m.kind === "day") return <div className="day">{m.text}</div>;
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
            alt="Imagen enviada por el cliente"
            loading="lazy"
          />
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
