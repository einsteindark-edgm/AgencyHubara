/**
 * Decisiones del motor en un turno del bot nuevo (motor de decisiones con
 * Jev), en frases (revisión 2026-09-29). Agrupadas por cuándo se tomaron (al
 * leer cada mensaje del cliente, durante el turno o en el mensaje de
 * complemento): qué revisó, qué decidió («Avisa al modelo que no aparece por
 * nombre en el catálogo: «jesús»»), quién (Jev, o la regla y por qué), qué decía la regla si no
 * coincidió y qué respondió Jev a cada pregunta. Así se ve, turno por turno,
 * si el bot nuevo corrió con Jev o cayó a las reglas. Compartido por el
 * laboratorio y Calidad LLM de Agents (2026-10-02).
 */

import {
  capabilityLabel,
  decidedByLabel,
  decisionSentence,
  decisionStageLabel,
  jevAnswers,
  jevFailed,
  type EngineDecisionView,
} from "@/shared/lib";

import { Chip } from "./QualityChips";

const TONE_TEXT = { neutral: "text-fg", warn: "text-warn", bad: "text-danger" } as const;

function summaryOf(decisions: EngineDecisionView[]): string {
  const n = decisions.length;
  const byJev = decisions.filter((d) => d.by === "jev").length;
  const failed = decisions.filter(jevFailed).length;
  const doubted = decisions.filter((d) => d.by === "respaldo" && d.reason === "duda").length;
  return [
    `Jev decidió ${byJev} de ${n}`,
    failed > 0 ? `${failed} ${failed === 1 ? "cayó" : "cayeron"} a la regla porque Jev falló` : null,
    doubted > 0 ? `en ${doubted} Jev dudó y decidió la regla` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function groups(decisions: EngineDecisionView[]): Array<[string, EngineDecisionView[]]> {
  const out = new Map<string, EngineDecisionView[]>();
  for (const d of decisions) {
    const label = decisionStageLabel(d);
    out.set(label, [...(out.get(label) ?? []), d]);
  }
  return [...out.entries()];
}

export function EngineDecisions({ decisions }: { decisions: EngineDecisionView[] }) {
  if (decisions.length === 0) return null;
  return (
    <section aria-label="Decisiones de Jev" className="grid gap-3 px-4 py-3 text-[12.5px]">
      <div>
        <p className="m-0 font-semibold text-fg">{summaryOf(decisions)}</p>
        <p className="m-0 mt-0.5 text-[11.5px] text-fg-muted">
          Cada cosa que el bot nuevo revisa la decide Jev; si Jev duda, no responde a tiempo o no hay nada que preguntarle, decide la regla de hoy.
        </p>
      </div>
      {groups(decisions).map(([stage, items]) => (
        <div key={stage} className="grid gap-1.5">
          <h4 className="m-0 text-[10px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{stage}</h4>
          <ul aria-label={stage} className="m-0 grid list-none gap-1.5 p-0">
            {items.map((d, k) => {
              const sentence = decisionSentence(d);
              const answers = jevAnswers(d);
              const quiet = d.by === "respaldo" && d.reason === "no_question";
              return (
                <li key={k} className={"grid gap-0.5 rounded-lg border border-line px-3 py-2 " + (quiet ? "opacity-70" : "bg-white/[0.02]")}>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="min-w-[132px] text-[11.5px] text-fg-muted">{capabilityLabel(d.capability)}</span>
                    <span className={"font-medium " + TONE_TEXT[sentence.tone]}>{sentence.text}</span>
                    <span className="ml-auto">
                      <Chip tone={d.by === "jev" ? "ok" : jevFailed(d) ? "warn" : "neutral"}>{decidedByLabel(d)}</Chip>
                    </span>
                  </div>
                  {d.rule !== undefined ? (
                    <p className="m-0 text-[11.5px] text-fg-muted">
                      <span className="text-fg-faint">La regla decía: </span>
                      <span>{decisionSentence({ ...d, value: d.rule }).text}</span>
                    </p>
                  ) : null}
                  {answers.map((a, j) => (
                    <p key={j} className="m-0 text-[11.5px] text-fg-muted">
                      {a}
                    </p>
                  ))}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}
