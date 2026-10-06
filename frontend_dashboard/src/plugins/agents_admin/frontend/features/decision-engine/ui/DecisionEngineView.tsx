/**
 * Pestaña «Motor de decisiones» de Calidad LLM (pedido del operador,
 * 2026-10-02): qué versión del motor corre la tienda (el paquete de decisión
 * y su versión, el oráculo, el perfil del turno; desde 2026-10-06 también el
 * paquete de la App Operador) y cada decisión que toma,
 * agrupada por la parte del software donde actúa — en el orden de la
 * conversación —, con lo que resuelve y quién la decide hoy (la regla de hoy,
 * Jev en sombra, Jev en las conversaciones de prueba o Jev).
 *
 * Los textos salen del catálogo del motor (`builtins.yaml: about`), no de la
 * pantalla: una decisión nueva llega con su explicación o no certifica.
 */

import type { ReactNode } from "react";

import {
  useDecisionEngine,
  type DecisionEngine,
  type EngineDecisionAbout,
  type PerceptionMode,
} from "@plugins/agents_admin/frontend/entities/perception-rollout";

const CARD = "rounded-lg border border-line p-3";
const H3 = "m-0 text-sm font-semibold text-fg";
const NOTE = "m-0 text-[11.5px] text-fg-muted";

/** Quién decide hoy, según el interruptor de la decisión. */
const MODE_TEXT: Record<PerceptionMode, string> = {
  off: "Decide la regla de hoy",
  shadow: "Jev en sombra: mide, decide la regla",
  canary: "Jev decide en las conversaciones de prueba",
  on: "Jev decide",
};

const MODE_TONE: Record<PerceptionMode, string> = {
  off: "border-line-strong bg-white/[0.03] text-fg-soft",
  shadow: "border-transparent bg-violet-soft text-violet",
  canary: "border-transparent bg-warn-soft text-warn",
  on: "border-transparent bg-ok-soft text-ok",
};

function oracleLabel(oracle: string): string {
  const match = /^jev-(.+)$/.exec(oracle);
  return match ? `Jev ${match[1]}` : oracle || "—";
}

function Version({ engine }: { engine: DecisionEngine }) {
  const { bundle } = engine;
  const configured = bundle.code_default && bundle.id && bundle.id !== bundle.code_default;
  const rows: Array<[string, ReactNode]> = [
    [
      "Paquete de decisión",
      <>
        <b className="font-semibold text-fg">{bundle.id || "—"}</b>
        {bundle.version !== null ? `, versión ${bundle.version}` : ""}
      </>,
    ],
    [
      "De dónde sale",
      configured
        ? `Lo eligió la configuración de esta tienda (Terraform); el que trae el código es «${bundle.code_default}».`
        : "Es el que trae el código: la tienda no eligió otro.",
    ],
    ["Oráculo", `${oracleLabel(bundle.oracle)}: el modelo al que el motor le pregunta`],
    ["Perfil del turno", engine.profile || "—"],
    ["Contrato del motor", bundle.engine_contract !== null ? String(bundle.engine_contract) : "—"],
  ];
  if (engine.bundles.length > 1) {
    rows.push([
      "Paquetes en el motor",
      <ul className="m-0 grid list-none gap-0.5 p-0">
        {engine.bundles.map((b) => (
          <li key={b.ref}>
            <b className="font-semibold text-fg">{b.ref}</b>
            {b.name ? ` — ${b.name}` : ""}
          </li>
        ))}
      </ul>,
    ]);
  }
  return (
    <section aria-label="Versión del motor de decisiones" className={CARD}>
      <h3 className={H3}>Versión del motor de decisiones</h3>
      <p className={"mt-1 " + NOTE}>
        El motor es el mismo código para toda tienda; lo que decide (las preguntas a Jev, los umbrales y las tablas) viene en el paquete de
        decisión, versionado. Otra pregunta u otro umbral es otra versión.
      </p>
      <dl className="m-0 mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-[12.5px] min-[640px]:grid-cols-[max-content_1fr]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-muted">{k}</dt>
            <dd className="m-0 text-fg-soft">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Decision({
  decision,
  nameOf,
  storeBundle,
}: {
  decision: EngineDecisionAbout;
  nameOf: (id: string) => string;
  storeBundle: string;
}) {
  const name = decision.name || decision.capability;
  // El paquete se dice solo si no es el de la tienda (el de arriba).
  const otherBundle = decision.bundle && decision.bundle !== storeBundle ? decision.bundle : null;
  return (
    <li aria-label={name} className="grid gap-1 rounded-lg border border-line bg-white/[0.02] px-3 py-2.5 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <b className="font-semibold text-fg">{name}</b>
        {decision.variant_of ? <span className="text-[11px] text-fg-faint">{`variante de «${nameOf(decision.variant_of)}»`}</span> : null}
        {otherBundle ? <span className="text-[11px] text-fg-faint">{`paquete ${otherBundle}`}</span> : null}
        <span
          className={
            "ml-auto inline-flex items-center whitespace-nowrap rounded-full border px-2 py-[4px] text-[11px] font-medium leading-none " +
            MODE_TONE[decision.mode]
          }
        >
          {MODE_TEXT[decision.mode]}
        </span>
      </div>
      <p className="m-0 text-fg-soft">{decision.solves}</p>
    </li>
  );
}

function Places({ engine }: { engine: DecisionEngine }) {
  const names = new Map(engine.decisions.map((d) => [d.capability, d.name || d.capability]));
  const nameOf = (id: string) => names.get(id) ?? id;
  return (
    <div className="grid gap-3">
      {engine.places.map((place) => {
        const decisions = engine.decisions.filter((d) => d.where.includes(place.id));
        if (decisions.length === 0) return null;
        return (
          <section key={place.id} aria-label={place.label} className={CARD}>
            <h3 className={H3}>{place.label}</h3>
            <ul className="m-0 mt-2 grid list-none gap-1.5 p-0">
              {decisions.map((d) => (
                <Decision key={d.capability} decision={d} nameOf={nameOf} storeBundle={engine.bundle.ref} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function Turn({ engine }: { engine: DecisionEngine }) {
  if (!engine.turn) return null;
  const { topics, questions } = engine.turn;
  return (
    <section aria-label="El turno del bot Jev" className={CARD}>
      <h3 className={H3}>Además, en cada turno del bot Jev</h3>
      <ul className="m-0 mt-2 grid list-disc gap-1 pl-5 text-[12.5px] text-fg-soft">
        <li>{`Antes de responder, Jev lee la ráfaga del cliente: qué asuntos plantea (${topics} asuntos) y qué le había preguntado el asesor (${questions} preguntas en total).`}</li>
        <li>Con eso el bot sabe qué herramienta usar para cada asunto (el catálogo, el envío, el pedido…) y en qué etapa de la compra está el cliente.</li>
        <li>Antes de enviar, Jev verifica que la respuesta atienda cada asunto; si falta uno, el bot manda un mensaje de complemento.</li>
      </ul>
    </section>
  );
}

export function DecisionEngineView() {
  const engine = useDecisionEngine();
  if (engine.isPending) return <p className="text-sm text-fg-muted">Cargando el motor de decisiones…</p>;
  if (engine.isError) return <p className="text-sm text-fg-muted">No se pudo leer el motor de decisiones.</p>;
  return (
    <div className="flex flex-col gap-3">
      <Version engine={engine.data} />
      <div>
        <h2 className="m-0 mb-1 text-[15px] font-semibold text-fg">Qué decide y dónde</h2>
        <p className={NOTE}>
          Cada decisión, en la parte del software donde actúa, en el orden de una conversación. Lo que dice a la derecha es quién la decide hoy: se
          cambia en el panel «Motor de decisiones» de la derecha.
        </p>
      </div>
      <Places engine={engine.data} />
      <Turn engine={engine.data} />
    </div>
  );
}
