/**
 * La ficha técnica de cada bot, a la derecha de su sección (operador,
 * 2026-10-08):
 *  - Colossus: el workflow nuevo, con Jev como clasificador y el motor de
 *    decisiones. La versión que corre (paquete, oráculo, perfil del turno) sale
 *    del mismo endpoint que la pestaña «Motor de decisiones», no de la pantalla.
 *  - Botsito: el workflow actual, sin clasificador; decide con código quemado.
 */

import type { ReactNode } from "react";

import { useDecisionEngine, type DecisionEngine, type PerceptionMode } from "@plugins/agents_admin/frontend/entities/perception-rollout";
import { BOT_LABEL, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";

const CARD = "rounded-lg border border-line p-3";
const H3 = "m-0 text-sm font-semibold text-fg";
const H4 = "m-0 mt-3 text-[12.5px] font-semibold text-fg";
const NOTE = "m-0 text-[11.5px] text-fg-muted";

function Facts({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="m-0 mt-2 grid grid-cols-1 gap-x-3 gap-y-1.5 text-[12.5px] min-[640px]:grid-cols-[max-content_1fr]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="m-0 text-fg-soft">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Shell({ bot, intro, children }: { bot: QualityBot; intro: string; children: ReactNode }) {
  return (
    <aside aria-label={`Especificaciones técnicas de ${BOT_LABEL[bot]}`} className={CARD + " min-[1200px]:sticky min-[1200px]:top-0"}>
      <h3 className={H3}>Especificaciones técnicas</h3>
      <p className={"mt-1 " + NOTE}>{intro}</p>
      {children}
    </aside>
  );
}

// ── Colossus ─────────────────────────────────────────────────────────────────

function oracleLabel(oracle: string): string {
  const match = /^jev-(.+)$/.exec(oracle);
  return match ? `Jev ${match[1]}` : oracle || "—";
}

const MODE_COUNT: ReadonlyArray<[PerceptionMode, string]> = [
  ["on", "las decide Jev"],
  ["canary", "Jev en las conversaciones de prueba"],
  ["shadow", "Jev en sombra"],
  ["off", "la regla de hoy"],
];

function decisionsSummary(engine: DecisionEngine): string {
  const total = engine.decisions.length;
  const parts = MODE_COUNT.map(([mode, label]) => [engine.decisions.filter((d) => d.mode === mode).length, label] as const)
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`);
  return `${total} ${total === 1 ? "decisión" : "decisiones"}${parts.length ? `: ${parts.join(" · ")}` : ""}.`;
}

function EngineFacts({ engine }: { engine: DecisionEngine }) {
  const { bundle, turn } = engine;
  const rows: Array<[string, ReactNode]> = [
    ["Workflow", "v2, el nuevo"],
    ["Clasificador", `${oracleLabel(bundle.oracle)}: el modelo al que el motor le pregunta`],
    [
      "Paquete de decisión",
      <>
        <b className="font-semibold text-fg">{bundle.id || "—"}</b>
        {bundle.version !== null ? `, versión ${bundle.version}` : ""}
      </>,
    ],
    ["Perfil del turno", engine.profile || "—"],
    ["Contrato del motor", bundle.engine_contract !== null ? String(bundle.engine_contract) : "—"],
    ["Decisiones", decisionsSummary(engine)],
  ];
  if (turn) {
    rows.push([
      "En cada turno",
      `Jev lee la ráfaga del cliente (${turn.topics} asuntos, ${turn.questions} preguntas) antes de responder y verifica que la respuesta atienda cada asunto antes de enviarla; si falta uno, sale un mensaje de complemento.`,
    ]);
  }
  return <Facts rows={rows} />;
}

function ColossusSpecs({ onOpenEngine }: { onOpenEngine: () => void }) {
  const engine = useDecisionEngine();
  let facts: ReactNode;
  if (engine.isPending) facts = <p className={"mt-2 " + NOTE}>Cargando la versión del motor…</p>;
  else if (engine.isError) facts = <p className={"mt-2 " + NOTE}>No se pudo leer la versión del motor de decisiones.</p>;
  else facts = <EngineFacts engine={engine.data} />;
  return (
    <Shell bot="nuevo" intro="El workflow nuevo del agente de ventas: Jev clasifica cada turno y las decisiones salen del motor de decisiones.">
      {facts}
      <h4 className={H4}>Cómo se versiona</h4>
      <ul className="m-0 mt-1 grid list-disc gap-1 pl-5 text-[12.5px] text-fg-soft">
        <li>El motor es el mismo código para toda tienda. Lo que decide —las preguntas a Jev, los umbrales y las tablas— viene en el paquete de decisión, en YAML.</li>
        <li>Un arreglo es otra pregunta, otro umbral, otra fila o un ejemplo: otra versión del paquete, no un «if» en el código.</li>
        <li>Cada versión se certifica antes de subir y el bot se controla solo por comando, nunca desde el dashboard.</li>
      </ul>
      <button
        type="button"
        onClick={onOpenEngine}
        className="mt-3 rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-fg transition hover:bg-white/5"
      >
        Ver cada decisión del motor
      </button>
    </Shell>
  );
}

// ── Botsito ──────────────────────────────────────────────────────────────────

function BotsitoSpecs() {
  return (
    <Shell bot="actual" intro="El workflow actual del agente de ventas, el de antes del motor de decisiones.">
      <Facts
        rows={[
          ["Workflow", "v1, el actual"],
          ["Clasificador", "Sin clasificador: nadie lee la ráfaga del cliente antes de responder ni verifica la respuesta antes de enviarla."],
          [
            "Decisiones",
            "Con código quemado: la compra confirmada, la baja, la zona de envío o para quién es el texto se deciden con «if», expresiones regulares y guardas en el código (al leer el mensaje, en las herramientas y antes de enviar).",
          ],
          ["Versiones", "Sin paquete de decisión: cambiar una decisión es cambiar el código y desplegar."],
        ]}
      />
    </Shell>
  );
}

export function BotSpecs({ bot, onOpenEngine }: { bot: QualityBot; onOpenEngine: () => void }) {
  return bot === "nuevo" ? <ColossusSpecs onOpenEngine={onOpenEngine} /> : <BotsitoSpecs />;
}
