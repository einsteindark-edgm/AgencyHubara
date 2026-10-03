/**
 * Detalle del paso seleccionado en el hilo del turno (plan del laboratorio
 * §11.1, diseño aprobado 2026-09-23). Encabezado con el tipo del paso en su
 * color, "N. título", carriles, tiempo y duración; después, las secciones de
 * cada tipo con lo que la traza trae de verdad (nada inventado: un campo que
 * falta no se muestra).
 *
 * Pedido del operador (2026-09-30, laboratorio 4567 t20): la ronda hacia el
 * modelo dice qué recibe; lo que el modelo pidió dice con qué y qué pasó
 * después en esa ronda; la herramienta dice cuál se ejecutó. Para eso el
 * detalle recibe la traza entera (`steps`).
 */

import type { ReactNode } from "react";

import {
  describeTool,
  GUARD_LABELS,
  jevChoiceLabel,
  jevQuestionLabel,
  modelRound,
  productName,
  roundInput,
  STEP_COLOR,
  type RoundInputItem,
  type SeqRow,
  type TraceStep,
} from "@/shared/lib";

interface Props {
  step: TraceStep;
  row: SeqRow;
  lanes: string[];
  /** La traza entera: la ronda del modelo se lee con sus pasos vecinos. */
  steps?: TraceStep[];
}

const TEXT_FATE: Record<string, string> = {
  none: "Sin texto",
  final: "Texto final",
  pre_tool_message: "Se envió antes de la herramienta",
  discarded_default_deny: "No se envió: venía junto a una herramienta",
  discarded_internal_tools: "No se envió: venía junto a una herramienta interna",
};

const CUT_REASON: Record<string, string> = {
  awaits_customer: "El bot quedó esperando la respuesta del cliente.",
  escalation: "El bot pasó la conversación a una persona.",
  send_reply: "La respuesta salió: el turno termina aquí.",
  tag_closure: "El bot cerró la conversación con una etiqueta.",
  checkpoint_a: "El cliente escribió mientras el modelo pensaba: el turno vuelve a empezar con el mensaje nuevo.",
  checkpoint_b: "El cliente escribió antes del envío: el turno vuelve a empezar con el mensaje nuevo.",
};

/** Qué tipo de burbuja salió. */
const BUBBLE_KIND: Record<string, string> = {
  text: "texto",
  products_list: "lista de productos",
  product: "producto",
  product_detail: "producto",
  image: "foto",
  interactive: "botones",
  buttons: "botones",
  flow: "formulario",
  reaction: "reacción",
};

/** Las partes de las instrucciones del bot, en palabras (las demás, por su archivo). */
const PART_NAMES: Record<string, string> = {
  "Agente de Hubara": "Presentación",
  "Retrieved Context": "Notas del turno",
  "Active Skills": "Guía de la etapa",
  Skills: "Lista de guías",
  Memory: "Memoria",
};

/** Protecciones que le dejan una nota al modelo para otra ronda. */
const NOTE_GUARDS = new Set(["contract_extra_round", "turn_policy_extra_round"]);

/** Argumentos de texto largo: van en su caja. */
const TEXT_ARGS = new Set(["text", "intro_text", "body", "customer_message", "caption", "closing_text"]);

/** Los argumentos más comunes, en palabras (los demás, por su nombre). */
const ARG_NAMES: Record<string, string> = {
  handle: "producto",
  handles: "productos",
  q: "búsqueda",
  category: "categoría",
  design: "diseño",
  variant_type: "tipo",
  options: "opciones",
  button_text: "botón",
  url: "enlace",
  tag: "etiqueta",
  code: "código",
  skill_name: "guía",
  limit: "máximo",
};

const VERIFY_DECISION: Record<string, string> = {
  send: "enviar",
  complement: "enviar y completar después",
  pending: "pendiente",
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

export function TraceStepDetail({ step, row, lanes, steps }: Props) {
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
      <Sections step={step} back={back} steps={steps ?? [step]} index={steps ? row.stepIndex : 0} />
    </div>
  );
}

function Sections({ step, back, steps, index }: { step: TraceStep; back: boolean; steps: TraceStep[]; index: number }) {
  switch (step.kind) {
    case "inbound":
      return <Inbound step={step} />;
    case "llm":
      return <Llm step={step} back={back} steps={steps} index={index} />;
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
              ["Esperó la foto", step.photo === true ? "sí: se estaba leyendo una foto del cliente" : null],
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

function Llm({ step, back, steps, index }: { step: TraceStep; back: boolean; steps: TraceStep[]; index: number }) {
  const tokensIn = num(step.tokens_in);
  const tokensOut = num(step.tokens_out);
  const fate = str(step.text_fate);
  const text = str(step.text);
  return (
    <>
      <Sec title="Llamada al modelo">
        <Kv
          rows={[
            ["Ronda", num(step.round)?.toString()],
            ["Modelo", str(step.model)],
            ["Tokens (entrada → salida)", tokensIn !== null || tokensOut !== null ? `${tokensIn !== null ? int(tokensIn) : "—"} → ${tokensOut !== null ? int(tokensOut) : "—"}` : null],
          ]}
        />
      </Sec>
      {back ? <Requested steps={steps} index={index} /> : <RoundInput steps={steps} index={index} />}
      {back && fate && fate !== "none" ? (
        <Sec title="Texto del modelo">
          <div className={"mb-1.5 text-[12px] " + (fate.startsWith("discarded") ? "text-warn" : "text-fg-soft")}>{TEXT_FATE[fate] ?? fate}</div>
          {text ? <Box>{text}</Box> : null}
        </Sec>
      ) : null}
    </>
  );
}

function historyPhrase(history: Record<string, number>): string {
  const total = Object.values(history).reduce((a, b) => a + b, 0);
  if (total === 0) return "sin mensajes anteriores";
  const plural = (n: number, one: string, many: string) => `${int(n)} ${n === 1 ? one : many}`;
  const parts = [
    history.user ? `${int(history.user)} del cliente` : null,
    history.assistant ? `${int(history.assistant)} del bot` : null,
    history.tool ? plural(history.tool, "resultado de herramientas", "resultados de herramientas") : null,
    history.system ? plural(history.system, "nota del sistema", "notas del sistema") : null,
  ].filter(Boolean);
  return `${plural(total, "mensaje", "mensajes")}: ${parts.join(", ")}`;
}

function InputItem({ item, exact }: { item: RoundInputItem; exact: boolean }) {
  const label =
    item.kind === "customer"
      ? exact
        ? "Mensaje del cliente, como lo lee el modelo"
        : "Mensaje del cliente"
      : item.kind === "tool"
        ? `Resultado de ${describeTool({ name: item.name ?? "" }).action}`
        : "Nota del bot";
  return (
    <div className="grid gap-1">
      <div className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-fg-muted">
        <span>{label}</span>
        {item.kind === "tool" && item.name ? <code className="font-mono text-[11px] text-cyan">{item.name}</code> : null}
      </div>
      {item.text ? <Box code={item.kind === "tool"}>{item.text}</Box> : <div className="text-[12px] italic text-fg-muted">(vacío)</div>}
    </div>
  );
}

/** Qué le manda el bot al modelo en esta ronda. */
function RoundInput({ steps, index }: { steps: TraceStep[]; index: number }) {
  const input = roundInput(steps, index);
  const rebuilt = input.exact ? null : (
    <div className="mb-2 text-[12px] text-fg-muted">Esta traza no guardó lo que recibió el modelo: reconstruido de la traza.</div>
  );
  if (input.first) {
    const parts = (input.system?.parts ?? []).map((p) => `${PART_NAMES[p.name] ?? p.name} ${int(p.chars)}`).join(" · ");
    return (
      <>
        <Sec title="Lo que recibe el modelo">
          {input.exact ? (
            <>
              <Kv
                rows={[
                  ["Instrucciones", `${int(input.system?.chars ?? 0)} caracteres`],
                  ["Historial", historyPhrase(input.history ?? {})],
                ]}
              />
              {parts ? <p className="m-0 mt-1.5 text-[11.5px] leading-relaxed text-fg-muted">{parts}</p> : null}
            </>
          ) : (
            <>
              {rebuilt}
              <div className="text-[12.5px] text-fg-soft">Las instrucciones del bot, el historial de la conversación y el mensaje del cliente.</div>
            </>
          )}
        </Sec>
        {input.notes ? (
          <Sec title="Notas del turno (van con las instrucciones)">
            <Box>{input.notes}</Box>
          </Sec>
        ) : null}
        {input.items.length > 0 ? (
          <div className="mt-3.5 grid gap-2.5">
            {input.items.map((it, k) => (
              <InputItem key={k} item={it} exact={input.exact} />
            ))}
          </div>
        ) : null}
      </>
    );
  }
  return (
    <Sec title="Lo nuevo en esta ronda">
      {rebuilt}
      <div className="grid gap-2.5">
        {input.items.length === 0 ? (
          <div className="text-[12.5px] text-fg-soft">Nada nuevo desde la ronda anterior.</div>
        ) : (
          input.items.map((it, k) => <InputItem key={k} item={it} exact={input.exact} />)
        )}
      </div>
      <div className="mt-2 text-[12px] text-fg-muted">Además recibe todo lo de las rondas anteriores: las instrucciones, el historial, el mensaje del cliente y lo que ya pidió.</div>
    </Sec>
  );
}

function argValue(key: string, value: unknown): string {
  if ((key === "handle" || key === "handles") && typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.filter((h): h is string => typeof h === "string").map(productName).join(", ");
    } catch {
      return productName(value);
    }
  }
  if (Array.isArray(value) && value.every((v) => typeof v === "string")) return value.map((v) => (key === "handles" ? productName(v) : v)).join(", ");
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** Los argumentos de una herramienta a la vista: los textos en su caja. */
function Args({ args }: { args: unknown }) {
  const entries = Object.entries(obj(args)).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return null;
  const texts = entries.filter(([k, v]) => TEXT_ARGS.has(k) && typeof v === "string");
  const rest = entries.filter(([k, v]) => !(TEXT_ARGS.has(k) && typeof v === "string"));
  return (
    <div className="grid gap-1.5">
      {texts.map(([k, v]) => (
        <Box key={k}>{String(v)}</Box>
      ))}
      {rest.length > 0 ? <Kv rows={rest.map(([k, v]) => [ARG_NAMES[k] ?? k, argValue(k, v)])} /> : null}
    </div>
  );
}

/** Lo que pidió el modelo, cada pedido con su ejecución, y lo que pasó después en la ronda. */
function Requested({ steps, index }: { steps: TraceStep[]; index: number }) {
  const round = modelRound(steps, index);
  const held = round.after.some((s) => s.kind === "guard" && (s.name === "contract_extra_round" || s.name === "send_reply_retry"));
  const answered = round.after.some((s) => s.kind === "cut" && s.reason === "send_reply");
  return (
    <>
      {round.calls.length > 0 ? (
        <Sec title="Pidió">
          <div className="grid gap-3">
            {round.calls.map((call, k) => {
              const s = call.step;
              const tool = describeTool({ name: call.name, args: s?.args, ok: (s?.ok ?? null) as boolean | null, error: str(s?.error), notes: s?.notes });
              const outcome = !s
                ? null
                : call.name === "send_reply" && !tool.failed
                  ? held
                    ? "no salió: el bot la retuvo"
                    : answered
                      ? "salió: es la respuesta del turno"
                      : tool.result
                  : tool.result;
              return (
                <div key={k} className="grid gap-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="rounded bg-cyan-soft px-1.5 py-0.5 text-[11.5px] text-cyan">{tool.action}</span>
                    <code className="font-mono text-[11px] text-fg-muted">{call.name}</code>
                  </div>
                  <Args args={s?.args} />
                  {outcome ? <div className={"text-[12px] " + (tool.failed || (held && call.name === "send_reply") ? "text-warn" : "text-fg-soft")}>{outcome}</div> : null}
                </div>
              );
            })}
          </div>
        </Sec>
      ) : null}
      {round.after.length > 0 ? (
        <Sec title="Después, en esta ronda">
          <div className="grid gap-2.5">
            {round.after.map((s, k) => (
              <AfterStep key={k} step={s} />
            ))}
          </div>
        </Sec>
      ) : null}
    </>
  );
}

function AfterStep({ step }: { step: TraceStep }) {
  if (step.kind === "guard") {
    const name = str(step.name) ?? "";
    const note = NOTE_GUARDS.has(name) ? str(step.after) : null;
    const shown = name === "variant_enumeration_guard" ? str(step.after) : null;
    return (
      <div className="grid gap-1">
        <div className="text-[12.5px] text-warn">{GUARD_LABELS[name] ?? name}</div>
        {note ? (
          <>
            <div className="text-[11.5px] text-fg-muted">Le dijo al modelo:</div>
            <Box>{note}</Box>
          </>
        ) : null}
        {shown ? (
          <>
            <div className="text-[11.5px] text-fg-muted">Lo que recibió el cliente:</div>
            <Box>{shown}</Box>
          </>
        ) : null}
      </div>
    );
  }
  if (step.kind === "cut") {
    const reason = str(step.reason);
    return <div className="text-[12.5px] text-fg-soft">{reason ? (CUT_REASON[reason] ?? reason) : "Fin del turno."}</div>;
  }
  return <div className="text-[12.5px] text-fg-soft">{`Vuelve a empezar con ${num(step.drained) ?? 0} mensaje(s) nuevo(s).`}</div>;
}

function Tool({ step, back }: { step: TraceStep; back: boolean }) {
  const args = step.args;
  const notes = list(step.notes).filter((n): n is string => typeof n === "string");
  const name = str(step.name) ?? "tool";
  const tool = describeTool({ name, args, ok: step.ok as boolean | null, error: str(step.error), notes });
  const hasArgs = args && typeof args === "object" && Object.keys(args).length > 0;
  return (
    <>
      {back ? (
        <Sec title="Qué hizo">
          <Kv
            rows={[
              ["Acción", tool.detail ? `${tool.action} · ${tool.detail}` : tool.action],
              ["Resultado", tool.result],
            ]}
          />
        </Sec>
      ) : (
        <>
          <Sec title="Herramienta que se ejecutó">
            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
              <code className="rounded bg-cyan-soft px-1.5 py-0.5 font-mono text-[11.5px] text-cyan">{name}</code>
              <span className="text-fg-soft">{tool.action}</span>
            </div>
          </Sec>
          {hasArgs ? (
            <Sec title="Con qué">
              <Args args={args} />
            </Sec>
          ) : null}
        </>
      )}
      <details className="mt-3.5 text-[12.5px]">
        <summary className="cursor-pointer select-none text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint">Datos técnicos</summary>
        {hasArgs ? (
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
      </details>
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
      <Sec title="Protección">
        <Kv
          rows={[
            ["Qué hizo", str(step.name) ? (GUARD_LABELS[String(step.name)] ?? String(step.name)) : null],
            ["Motivo", str(step.reason)],
            ["Acciones", actions.length ? actions.join(", ") : null],
            ["Herramientas", tools.length ? tools.map((n) => describeTool({ name: n }).action).join(", ") : null],
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
          <Box>{after === "" ? (before ? "(vacío: la protección se llevó el texto)" : "(vacío)") : after}</Box>
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
        <Sec title="Herramientas pedidas">
          <Box>{tools.map((n) => describeTool({ name: n }).action).join(", ")}</Box>
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
      <Sec title="Jev">
        <Kv
          rows={[
            ["Modelo", str(step.model)],
            ["Versión del motor", str(step.profile)],
            ["Decisión", str(step.decision) ? (VERIFY_DECISION[String(step.decision)] ?? String(step.decision)) : null],
            ["Umbral de confianza", threshold !== null ? prob(threshold) : null],
            ["Costo", cost !== null ? usd(cost) : null],
            ["Error", str(step.error)],
          ]}
        />
      </Sec>
      {answers.length > 0 ? (
        <Sec title="Preguntas y respuestas">
          <table className="w-full border-collapse text-[12px]">
            <tbody>
              {answers.map((a, k) => {
                const p = num(a.p);
                const picked = a.picked === true;
                const id = str(a.q);
                // Pregunta de opción (qué preguntó el asesor, el asunto de un
                // mensaje): lo que eligió Jev y qué tan seguro estaba.
                const choice = str(a.choice);
                const confidence = num(a.confidence);
                const shown = p ?? (choice ? confidence : null);
                return (
                  <tr key={k}>
                    <td title={id ?? undefined} className={"w-[46%] py-0.5 pr-2 align-top " + (picked ? "font-semibold text-fg" : "text-fg-soft")}>
                      {id ? jevQuestionLabel(id) : `pregunta ${k + 1}`}
                    </td>
                    <td className="w-full py-0.5">
                      {choice && p === null ? (
                        <span className="text-fg-soft">{jevChoiceLabel(id ?? "", choice)}</span>
                      ) : (
                        <span className="relative block h-2 overflow-hidden rounded bg-white/[0.06]" aria-hidden="true">
                          <b className="absolute inset-y-0 left-0 rounded" style={{ width: `${Math.round((p ?? 0) * 100)}%`, background: picked ? "var(--color-violet)" : "var(--color-neutral)" }} />
                          {threshold !== null ? <em className="absolute -inset-y-0.5 w-0.5 bg-white/55" style={{ left: `${Math.round(threshold * 100)}%` }} /> : null}
                        </span>
                      )}
                    </td>
                    <td className="w-11 py-0.5 pl-2 text-right tabular-nums text-fg-soft">{shown !== null ? prob(shown) : "—"}</td>
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
                <span className="text-fg-muted">{str(b.kind) ? (BUBBLE_KIND[String(b.kind)] ?? String(b.kind)) : "mensaje"}</span>
                <span className={tone}>{label}</span>
              </div>
              {str(b.text) ? <Box>{String(b.text)}</Box> : null}
            </div>
          );
        })}
      </div>
    </Sec>
  );
}
