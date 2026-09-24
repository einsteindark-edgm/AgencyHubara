/**
 * Detalle del paso seleccionado en el hilo del turno (plan del laboratorio
 * §11.1, diseño aprobado 2026-09-23). Encabezado con el tipo del paso en su
 * color, "N. título", carriles, tiempo y duración; después, las secciones de
 * cada tipo con lo que la traza trae de verdad (nada inventado: un campo que
 * falta no se muestra).
 */

import type { ReactNode } from "react";

import { STEP_COLOR, type SeqRow, type TraceStep } from "@/shared/lib";

interface Props {
  step: TraceStep;
  row: SeqRow;
  lanes: string[];
}

const TEXT_FATE: Record<string, string> = {
  none: "Sin texto",
  final: "Texto final",
  pre_tool_message: "Enviado antes de la tool",
  discarded_default_deny: "Descartado: venía junto a una tool (default-deny)",
  discarded_internal_tools: "Descartado: venía junto a una tool interna",
};

const CUT_REASON: Record<string, string> = {
  awaits_customer: "La tool espera la respuesta del cliente: el turno termina acá.",
  escalation: "Escalación a un humano: el turno termina acá.",
  send_reply: "send_reply entregó la respuesta: el turno termina acá.",
  tag_closure: "La tool cerró la conversación con un tag.",
  checkpoint_a: "Corrientazo A: el cliente escribió mientras el LLM pensaba; el turno se reinicia con el mensaje nuevo.",
  checkpoint_b: "Corrientazo B: el cliente escribió antes del envío; el turno se reinicia con el mensaje nuevo.",
};

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function int(n: number): string {
  return n.toLocaleString("es-CO");
}

function prob(p: number): string {
  return p.toFixed(2).replace(".", ",");
}

function usd(n: number): string {
  return `US$${n.toFixed(4).replace(".", ",")}`;
}

export function TraceStepDetail({ step, row, lanes }: Props) {
  const color = STEP_COLOR[row.status];
  const back = row.to < row.from && row.from !== 0;
  const meta = [`${lanes[row.from]} → ${lanes[row.to]}`, row.t, row.dur ? `duró ${row.dur}` : ""].filter(Boolean).join(" · ");
  return (
    <div>
      <p className="mb-1.5 text-[10px] font-semibold uppercase leading-none tracking-[0.08em]" style={{ color }}>
        {row.kind}
      </p>
      <h4 className="mb-1 text-[15px] font-semibold">
        {row.index + 1}. {row.title}
      </h4>
      <div className="mb-3 text-xs tabular-nums text-fg-muted">{meta}</div>
      <Sections step={step} back={back} />
    </div>
  );
}

function Sections({ step, back }: { step: TraceStep; back: boolean }) {
  switch (step.kind) {
    case "inbound":
      return <Inbound step={step} />;
    case "llm":
      return <Llm step={step} back={back} />;
    case "tool":
      return <Tool step={step} back={back} />;
    case "guard":
      return <Guard step={step} />;
    case "cut":
      return <Cut step={step} />;
    case "restart":
      return (
        <Sec title="Reinicio">
          <Kv
            rows={[
              ["Intento", num(step.attempt)?.toString()],
              ["Mensajes nuevos", num(step.drained)?.toString()],
              ["Motivo", str(step.reason) ? (CUT_REASON[String(step.reason)] ?? String(step.reason)) : null],
            ]}
          />
        </Sec>
      );
    case "perception":
    case "verify":
      return <Classifier step={step} />;
    case "plan":
      return <Plan step={step} />;
    case "outbound":
      return <Outbound step={step} />;
    case "truncated":
      return <Sec title="Traza recortada">{`Se omitieron ${num(step.dropped) ?? "varios"} pasos por el tope de la traza.`}</Sec>;
    default:
      return null;
  }
}

function Sec({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-3.5">
      <h5 className="mb-1.5 text-[10.5px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">{title}</h5>
      {typeof children === "string" ? <div className="text-[12.5px] text-fg-soft">{children}</div> : children}
    </div>
  );
}

function Box({ children, code = false }: { children: ReactNode; code?: boolean }) {
  return (
    <div
      className={
        "whitespace-pre-wrap break-words rounded-lg border border-line bg-black/25 px-3 py-2.5 text-fg-soft " +
        (code ? "font-mono text-[11.5px] leading-[1.55]" : "text-[12.5px]")
      }
    >
      {children}
    </div>
  );
}

function Kv({ rows }: { rows: Array<[string, string | null | undefined]> }) {
  const shown = rows.filter((r): r is [string, string] => typeof r[1] === "string" && r[1] !== "");
  if (shown.length === 0) return null;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12.5px]">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="m-0 tabular-nums text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Inbound({ step }: { step: TraceStep }) {
  const messages = list(step.messages).map(obj);
  const first = num(messages[0]?.ts_ms);
  return (
    <Sec title={messages.length > 1 ? `El cliente escribió ${messages.length} mensajes` : "Mensaje del cliente"}>
      <div className="grid gap-1.5">
        {messages.map((m, k) => {
          const ts = num(m.ts_ms);
          const offset = k > 0 && ts !== null && first !== null ? `+${Math.round((ts - first) / 1000)} s` : null;
          return (
            <div key={k} className="grid grid-cols-[auto_1fr] items-start gap-2">
              <span className="pt-2.5 text-[11px] tabular-nums text-fg-faint">
                [{k + 1}]{offset ? " " : ""}
                {offset ? <span>{offset}</span> : null}
              </span>
              <Box>{str(m.text) ?? (str(m.kind) ? `(${String(m.kind)})` : "")}</Box>
            </div>
          );
        })}
      </div>
    </Sec>
  );
}

function Llm({ step, back }: { step: TraceStep; back: boolean }) {
  const tokensIn = num(step.tokens_in);
  const tokensOut = num(step.tokens_out);
  const tools = list(step.tool_calls).filter((t): t is string => typeof t === "string");
  const fate = str(step.text_fate);
  const text = str(step.text);
  return (
    <>
      <Sec title="Llamada">
        <Kv
          rows={[
            ["Ronda", num(step.round)?.toString()],
            ["Modelo", str(step.model)],
            ["Tokens (entrada → salida)", tokensIn !== null || tokensOut !== null ? `${tokensIn !== null ? int(tokensIn) : "—"} → ${tokensOut !== null ? int(tokensOut) : "—"}` : null],
            ["Fin", back ? str(step.finish) : null],
          ]}
        />
      </Sec>
      {back && tools.length > 0 ? (
        <Sec title="Pidió">
          <div className="flex flex-wrap gap-1.5">
            {tools.map((t, k) => (
              <code key={k} className="rounded bg-cyan-soft px-1.5 py-0.5 font-mono text-[11.5px] text-cyan">
                {t}
              </code>
            ))}
          </div>
        </Sec>
      ) : null}
      {back && fate && fate !== "none" ? (
        <Sec title="Texto del LLM">
          <div className={"mb-1.5 text-[12px] " + (fate.startsWith("discarded") ? "text-warn" : "text-fg-soft")}>{TEXT_FATE[fate] ?? fate}</div>
          {text ? <Box>{text}</Box> : null}
        </Sec>
      ) : null}
    </>
  );
}

function Tool({ step, back }: { step: TraceStep; back: boolean }) {
  const args = step.args;
  const notes = list(step.notes).filter((n): n is string => typeof n === "string");
  const ok = step.ok;
  return (
    <>
      <Sec title="Ejecución">
        <Kv
          rows={[
            ["Nombre", str(step.name)],
            ["Resultado", ok === true ? "ok" : ok === false ? "rechazada" : null],
            ["Motivo", ok === false ? str(step.error) : null],
          ]}
        />
      </Sec>
      {args && typeof args === "object" && Object.keys(args).length > 0 ? (
        <Sec title="Argumentos">
          <Box code>{JSON.stringify(args, null, 2)}</Box>
        </Sec>
      ) : null}
      {back && notes.length > 0 ? (
        <Sec title="Notas">
          <ul className="m-0 grid list-disc gap-1 pl-4 text-[12.5px] text-fg-soft">
            {notes.map((n, k) => (
              <li key={k}>{n}</li>
            ))}
          </ul>
        </Sec>
      ) : null}
      {back && str(step.excerpt) ? (
        <Sec title="Extracto del resultado">
          <Box code>{String(step.excerpt)}</Box>
        </Sec>
      ) : null}
    </>
  );
}

function Guard({ step }: { step: TraceStep }) {
  const before = typeof step.before === "string" ? step.before : null;
  const after = typeof step.after === "string" ? step.after : null;
  const actions = list(step.actions).filter((a): a is string => typeof a === "string");
  const tools = list(step.tools).filter((a): a is string => typeof a === "string");
  return (
    <>
      <Sec title="Guarda">
        <Kv
          rows={[
            ["Nombre", str(step.name)],
            ["Motivo", str(step.reason)],
            ["Acciones", actions.length ? actions.join(", ") : null],
            ["Tools", tools.length ? tools.join(", ") : null],
          ]}
        />
      </Sec>
      {before !== null ? (
        <Sec title="Antes">
          <Box>{before === "" ? "(vacío)" : before}</Box>
        </Sec>
      ) : null}
      {after !== null ? (
        <Sec title="Después">
          <Box>{after === "" ? (before ? "(vacío: la guarda se llevó el texto)" : "(vacío)") : after}</Box>
        </Sec>
      ) : null}
    </>
  );
}

function Cut({ step }: { step: TraceStep }) {
  const reason = str(step.reason);
  const tools = list(step.tools).filter((a): a is string => typeof a === "string");
  return (
    <>
      <Sec title="Por qué terminó el turno">{reason ? (CUT_REASON[reason] ?? reason) : "Sin motivo registrado."}</Sec>
      {tools.length ? (
        <Sec title="Tools del lote">
          <Box code>{tools.join(", ")}</Box>
        </Sec>
      ) : null}
      {str(step.text) ? (
        <Sec title="Texto al cortar">
          <Box>{String(step.text)}</Box>
        </Sec>
      ) : null}
    </>
  );
}

function Classifier({ step }: { step: TraceStep }) {
  const answers = list(step.answers).map(obj);
  const threshold = num(step.threshold);
  const cost = num(step.cost_usd);
  return (
    <>
      <Sec title="Clasificador">
        <Kv
          rows={[
            ["Modelo", str(step.model)],
            ["Perfil", str(step.profile)],
            ["Decisión", str(step.decision)],
            ["Umbral", threshold !== null ? prob(threshold) : null],
            ["Costo", cost !== null ? usd(cost) : null],
            ["Error", str(step.error)],
          ]}
        />
      </Sec>
      {answers.length > 0 ? (
        <Sec title="Respuestas">
          <table className="w-full border-collapse text-[12px]">
            <tbody>
              {answers.map((a, k) => {
                const p = num(a.p);
                const picked = a.picked === true;
                return (
                  <tr key={k}>
                    <td className={"max-w-[150px] truncate py-0.5 pr-2 " + (picked ? "font-semibold text-fg" : "text-fg-soft")}>{str(a.q) ?? `pregunta ${k + 1}`}</td>
                    <td className="w-full py-0.5">
                      <span className="relative block h-2 overflow-hidden rounded bg-white/[0.06]" aria-hidden="true">
                        <b className="absolute inset-y-0 left-0 rounded" style={{ width: `${Math.round((p ?? 0) * 100)}%`, background: picked ? "var(--color-violet)" : "var(--color-neutral)" }} />
                        {threshold !== null ? <em className="absolute -inset-y-0.5 w-0.5 bg-white/55" style={{ left: `${Math.round(threshold * 100)}%` }} /> : null}
                      </span>
                    </td>
                    <td className="w-11 py-0.5 pl-2 text-right tabular-nums text-fg-soft">{p !== null ? prob(p) : "—"}</td>
                    <td className="w-[18px] py-0.5 text-center">{picked ? "✓" : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Sec>
      ) : null}
    </>
  );
}

function Plan({ step }: { step: TraceStep }) {
  const items = list(step.checklist).map(obj);
  return (
    <Sec title="Asuntos que el bot debe cubrir">
      <ul className="m-0 grid list-disc gap-1 pl-4 text-[12.5px] text-fg-soft">
        {items.map((it, k) => (
          <li key={k}>{str(it.label) ?? str(it.topic) ?? `asunto ${k + 1}`}</li>
        ))}
      </ul>
    </Sec>
  );
}

function Outbound({ step }: { step: TraceStep }) {
  const bubbles = list(step.bubbles).map(obj);
  return (
    <Sec title={bubbles.length === 1 ? "Lo que salió" : `Lo que salió (${bubbles.length})`}>
      <div className="grid gap-2">
        {bubbles.map((b, k) => {
          const delivered = b.delivered;
          const [label, tone] =
            delivered === true ? ["entregada", "text-ok"] : delivered === false ? ["no salió", "text-danger"] : ["sin confirmación", "text-fg-muted"];
          return (
            <div key={k} className="grid gap-1">
              <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                <span className="text-fg-muted">{str(b.kind) ?? "mensaje"}</span>
                <span className={tone}>{label}</span>
                {str(b.wamid) ? <code className="truncate font-mono text-[10.5px] text-fg-faint">{String(b.wamid)}</code> : null}
              </div>
              {str(b.text) ? <Box>{String(b.text)}</Box> : null}
            </div>
          );
        })}
      </div>
    </Sec>
  );
}
