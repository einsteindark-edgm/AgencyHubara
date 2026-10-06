/**
 * El hilo de una conversación con cada turno del bot (laboratorio §11 y
 * Calidad LLM de Agents, 2026-10-02): burbujas como en Chats, la ráfaga del
 * cliente agrupada y el botón «Ver hilo del turno» con el resultado de ESE
 * turno. Recibe los ítems ya armados (`productionThreadView` de `@/shared/lib`
 * o la vista simulada del laboratorio).
 */

import { formatDayLabelEs, type QualityVerdict, type ReplyItem, type ThreadItem, type ThreadTurnView } from "@/shared/lib";

import { VerdictBadge } from "./QualityChips";

/** Etiqueta de un mensaje que escribió una persona del equipo (no el bot). */
const TEAM_LABEL = "Persona del equipo";
/** Etiqueta del mensaje que el bot nuevo mandó después para cubrir lo que faltó. */
const COMPLEMENT_LABEL = "Mensaje de complemento";

/** El punto del botón de cada turno, del color de su resultado (el mismo de la ventana del turno). */
const VERDICT_DOT: Record<QualityVerdict, string> = {
  PASA: "bg-ok",
  ALERTA: "bg-warn",
  FALLA: "bg-danger",
  SIN_DATOS: "bg-neutral",
};

export function Bubble({
  dir,
  text,
  time,
  hasImage,
  byHuman,
  complement,
}: {
  dir: "in" | "out" | "comp";
  text: string;
  time: string;
  hasImage?: boolean;
  byHuman?: boolean;
  complement?: boolean;
}) {
  const cls =
    dir === "in"
      ? "self-start rounded-bl-[5px] bg-bubble-out text-fg"
      : dir === "comp"
        ? "self-end border border-info/40 bg-info-soft text-white"
        : "self-end rounded-br-[5px] bg-bubble-in text-white";
  return (
    <div
      className={
        "max-w-[82%] whitespace-pre-line break-words rounded-[14px] px-3 py-2 text-[13.5px] leading-[1.42] " +
        cls +
        (byHuman ? " ring-2 ring-warn/60" : "") +
        (complement ? " border border-dashed border-white/50" : "")
      }
    >
      {byHuman ? <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{TEAM_LABEL}</div> : null}
      {complement ? <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{COMPLEMENT_LABEL}</div> : null}
      {text || (hasImage ? "📷 Foto" : "")}
      {time ? <div className={"mt-[3px] text-right text-[10px] tabular-nums " + (dir === "in" ? "text-fg-faint" : "text-white/75")}>{time}</div> : null}
    </div>
  );
}

/** Lo que respondió el bot en un turno, alineado como en el hilo. */
export function ReplyBubbles({ replies }: { replies: ReplyItem[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {replies.map((r, k) =>
        r.dir === "note" || r.dir === "system" ? (
          <p key={k} className="m-0 self-end text-[12px] italic text-fg-muted">{r.text}</p>
        ) : (
          <p
            key={k}
            className={
              "m-0 max-w-[82%] self-end whitespace-pre-line break-words rounded-[14px] px-3 py-2 text-[13.5px] leading-[1.42] " +
              (r.dir === "comp" ? "border border-info/40 bg-info-soft text-white" : "rounded-br-[5px] bg-bubble-in text-white") +
              (r.byHuman ? " ring-2 ring-warn/60" : "") +
              (r.complement ? " border border-dashed border-white/50" : "")
            }
          >
            {r.byHuman ? <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{TEAM_LABEL}</span> : null}
            {r.complement ? <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{COMPLEMENT_LABEL}</span> : null}
            {r.text || (r.hasImage ? "📷 Foto" : "")}
          </p>
        ),
      )}
    </div>
  );
}

export function ThreadRow<T extends ThreadTurnView>({
  item,
  evaluating,
  onOpenTurn,
}: {
  item: ThreadItem<T>;
  evaluating: boolean;
  onOpenTurn: (turn: T) => void;
}) {
  switch (item.type) {
    case "day":
      return <div className="self-center px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{formatDayLabelEs(item.day)}</div>;
    case "msg":
      if (item.dir === "system") return <div className="self-center text-[11.5px] text-fg-muted">{item.text}</div>;
      return <Bubble dir={item.dir} text={item.text} time={item.time} hasImage={item.hasImage} byHuman={item.byHuman} complement={item.complement} />;
    case "note":
      return <div className="self-end text-[11.5px] italic text-fg-muted">{item.text}</div>;
    case "burst":
      return (
        <button
          type="button"
          aria-label={`Ver el hilo de la ráfaga de ${item.messages.length} mensajes`}
          onClick={() => onOpenTurn(item.turn)}
          className="flex w-full gap-2 self-start border-0 bg-transparent p-0 text-left text-inherit focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span aria-hidden="true" className="w-[3px] flex-none rounded-[3px] bg-violet opacity-80" />
          <span className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase leading-none tracking-[0.06em] text-violet">
              {`${item.messages.length} mensajes seguidos${item.spanS > 0 ? ` en ${item.spanS} s` : ""}`}
            </span>
            {item.messages.map((m, k) => (
              <span key={k} className="block max-w-[90%] whitespace-pre-line break-words rounded-[14px] rounded-bl-[5px] bg-bubble-out px-3 py-2 text-[13.5px] leading-[1.42] text-fg">
                {m.text}
                {m.time ? <span className="mt-[3px] block text-right text-[10px] tabular-nums text-fg-faint">{m.time}</span> : null}
              </span>
            ))}
          </span>
        </button>
      );
    case "chip":
      return (
        <button
          type="button"
          onClick={() => onOpenTurn(item.turn)}
          className="inline-flex items-center gap-2 self-end rounded-full border border-line-strong bg-white/[0.06] px-3 py-[7px] text-xs font-medium leading-none text-fg hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <i aria-hidden="true" className={"inline-block h-2 w-2 rounded-full " + VERDICT_DOT[item.verdict ?? "SIN_DATOS"]} />
          Ver hilo del turno <small className="text-[11.5px] text-fg-muted">{item.sub}</small>
          {item.verdict ? (
            <VerdictBadge verdict={item.verdict} />
          ) : evaluating ? (
            // Mientras carga no se sabe: «sin evaluar» sería falso.
            <small aria-label="Cargando la evaluación" className="text-[11px] text-fg-faint">…</small>
          ) : (
            <small className="text-[11px] text-fg-faint">sin evaluar</small>
          )}
        </button>
      );
  }
}

/** El hilo entero: un `ThreadRow` por ítem, con su scroll. */
export function ConversationThread<T extends ThreadTurnView>({
  items,
  evaluating,
  onOpenTurn,
}: {
  items: ThreadItem<T>[];
  evaluating: boolean;
  onOpenTurn: (turn: T) => void;
}) {
  return (
    <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto bg-canvas px-4 py-3.5">
      {items.map((it) => (
        <ThreadRow key={it.key} item={it} evaluating={evaluating} onOpenTurn={onOpenTurn} />
      ))}
    </div>
  );
}
