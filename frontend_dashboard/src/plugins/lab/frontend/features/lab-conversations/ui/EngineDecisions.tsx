/**
 * Decisiones del motor en un turno del bot nuevo (motor de decisiones con Jev):
 * cada capacidad que decidió en la ráfaga (lecturas del ingest), en el turno
 * (tools y egreso) o en el complemento, quién decidió (Jev, la regla, el piso
 * de la regla o la regla porque Jev falló o dudó) y qué dijeron la regla y Jev.
 * Así se ve, turno por turno, si el bot nuevo corrió con Jev o cayó a las reglas.
 */

import {
  capabilityLabel,
  Chip,
  decidedByLabel,
  decisionStageLabel,
  formatDecisionValue,
  jevFailed,
  type EngineDecision,
} from "@plugins/lab/frontend/entities/lab-run";

const NUMBER = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 });

function answerLabel(d: EngineDecision): string {
  return d.answers
    .slice(0, 3)
    .map((a) => (a.choice !== undefined ? `${a.choice}${a.confidence !== undefined ? ` · ${NUMBER.format(a.confidence)}` : ""}` : a.p !== undefined ? `p ${NUMBER.format(a.p)}` : ""))
    .filter(Boolean)
    .join("; ");
}

function summaryOf(decisions: EngineDecision[]): string {
  const n = decisions.length;
  const byJev = decisions.filter((d) => d.by === "jev").length;
  const failed = decisions.filter(jevFailed).length;
  return [
    `${n} ${n === 1 ? "decisión" : "decisiones"}`,
    `${byJev} de Jev`,
    failed > 0 ? `${failed} ${failed === 1 ? "cayó" : "cayeron"} a la regla porque Jev falló` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function EngineDecisions({ decisions }: { decisions: EngineDecision[] }) {
  if (decisions.length === 0) return null;
  return (
    <section aria-label="Decisiones de Jev" className="px-4 py-2.5 text-[12.5px]">
      <details open>
        <summary className="cursor-pointer select-none">
          <span className="mr-2 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">Motor de decisiones</span>
          <span>{summaryOf(decisions)}</span>
        </summary>
        <table className="mt-2 w-full border-collapse text-left tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
              <th className="py-1 pr-3 font-semibold">Etapa</th>
              <th className="py-1 pr-3 font-semibold">Capacidad</th>
              <th className="py-1 pr-3 font-semibold">Decidió</th>
              <th className="py-1 pr-3 font-semibold">Resultado</th>
              <th className="py-1 pr-3 font-semibold">La regla decía</th>
              <th className="py-1 font-semibold">Jev</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d, k) => (
              <tr key={k} className="border-t border-line align-top">
                <td className="py-1 pr-3 text-fg-muted">{decisionStageLabel(d)}</td>
                <td className="py-1 pr-3">{capabilityLabel(d.capability)}</td>
                <td className="py-1 pr-3">
                  <Chip tone={d.by === "jev" ? "ok" : jevFailed(d) ? "warn" : "neutral"}>{decidedByLabel(d)}</Chip>
                </td>
                <td className="py-1 pr-3">{formatDecisionValue(d.value)}</td>
                <td className="py-1 pr-3 text-fg-muted">{d.rule !== undefined ? formatDecisionValue(d.rule) : ""}</td>
                <td className="py-1 text-fg-muted">{answerLabel(d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
