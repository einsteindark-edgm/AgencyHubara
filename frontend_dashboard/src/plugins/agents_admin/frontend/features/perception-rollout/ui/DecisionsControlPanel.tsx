/**
 * Motor de decisiones — diseño v2 §08 y §11, fase F7.
 *
 * Cada capacidad (una pieza de código quemado que ahora decide el motor) con
 * su interruptor: reglas (la de hoy) → sombra (Jev al lado, sin actuar) →
 * canary (números de prueba y porcentaje) → Jev. Y la versión del workflow
 * de ventas: V2 por canary antes de todos. Todo dentro de los techos de
 * Terraform. Bajar siempre está (vuelta atrás); subir se apaga si falta la
 * vara y dice qué falta; canary y Jev llegan a clientes, así que piden
 * confirmar en dos pasos. El servidor vuelve a chequear: el panel no decide.
 */

import { useState } from "react";

import {
  usePerceptionRollout,
  useSetCapabilityMode,
  useSetWorkflowMode,
  type CapabilityControl,
  type PerceptionMode,
  type WorkflowControl,
  type WorkflowMode,
} from "@plugins/agents_admin/frontend/entities/perception-rollout";
import { ApiError } from "@/shared/sdk";
import { MacButton, Panel } from "@/shared/ui";

const MODES: PerceptionMode[] = ["off", "shadow", "canary", "on"];

const MODE_LABEL: Record<PerceptionMode, string> = {
  off: "Reglas",
  shadow: "Sombra",
  canary: "Canary",
  on: "Jev",
};

/** Qué decide cada capacidad, en palabras del operador. */
const CAPABILITY_LABEL: Record<string, string> = {
  compra: "Compra",
  retoma: "Retoma",
  baja: "Baja",
  cupon: "Cupón",
  fuera_de_catalogo: "Fuera de catálogo",
  cantidad: "Cantidad",
  categoria: "Categoría",
  familia_de_color: "Familia de color",
  item_del_pedido: "Ítem del pedido",
  zona_de_envio: "Zona de envío",
  datos: "Datos de envío",
  persona: "Persona",
  enumeracion: "Enumeración",
  monto: "Monto",
  selector: "Selector",
  contactar: "Contactar",
  afirmacion: "Afirmación",
  destinatario: "Destinatario",
  rescate: "Rescate",
  portavelas: "Portavelas",
  saludo: "Saludo",
};

const CAPABILITY_HINT: Record<string, string> = {
  compra: "¿el cliente confirma la compra?",
  retoma: "¿aplazó la conversación y hasta cuándo?",
  baja: "¿pide no recibir más mensajes?",
  cupon: "¿habla de un cupón o una promoción?",
  fuera_de_catalogo: "¿pide algo que no vendemos?",
  cantidad: "¿cuántas unidades dijo?",
  categoria: "¿qué categoría busca?",
  familia_de_color: "¿a qué familia pertenece el color?",
  item_del_pedido: "¿a qué producto del pedido va el dato?",
  zona_de_envio: "¿la ciudad paga tarifa de Bogotá o nacional?",
  datos: "¿el cliente dio el dato que se va a guardar?",
  persona: "¿el texto delata que atiende un bot?",
  enumeracion: "¿el texto lista variantes para el selector?",
  monto: "¿la oración cotiza el precio de un producto?",
  selector: "¿los botones eligen producto o variante?",
  contactar: "¿sobra el mensaje proactivo de remarketing?",
  afirmacion: "¿afirma algo sin consultarlo? (solo se mide)",
  destinatario: "¿el texto es para el cliente? (V2)",
  rescate: "¿qué párrafos se salvan? (V2)",
  portavelas: "¿promete portavelas que no van? (V2)",
  saludo: "¿el turno ya saluda? (V2)",
};

const WORKFLOW_LABEL: Record<WorkflowMode, string> = {
  off: "V1",
  canary: "V2 en canary",
  on: "V2 para todos",
};

type Pending =
  | { kind: "capability"; capability: string; mode: PerceptionMode }
  | { kind: "workflow"; mode: WorkflowMode };

function rank(mode: string): number {
  return MODES.indexOf(mode as PerceptionMode);
}

function failingDetails(control: CapabilityControl | WorkflowControl, target: string): string[] {
  return (control.readiness[target] ?? []).filter((c) => !c.ok).map((c) => c.detail || c.code);
}

function errorLines(error: unknown): string[] {
  if (error instanceof ApiError) {
    if (error.status === 504 || error.status === 0) return ["El cambio puede haberse aplicado; releyendo el estado."];
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { readiness?: { ok?: boolean; detail?: string }[]; message?: string };
      const failing = (d.readiness ?? []).filter((c) => !c.ok && c.detail).map((c) => c.detail as string);
      if (failing.length > 0) return failing;
      if (d.message) return [d.message];
    }
    return [`Error ${error.status}`];
  }
  return ["El cambio puede haberse aplicado; releyendo el estado."];
}

function CapabilityRow({
  name,
  control,
  busy,
  onPick,
}: {
  name: string;
  control: CapabilityControl;
  busy: boolean;
  onPick: (mode: PerceptionMode) => void;
}) {
  const label = CAPABILITY_LABEL[name] ?? name;
  const current = control.mode;
  const capped = rank(current) > rank(control.ceiling);
  const next = MODES.find((m) => rank(m) > rank(current) && (control.can[m] ?? []).length > 0);
  const missing = next ? failingDetails(control, next) : [];
  return (
    <div role="group" aria-label={label} style={{ padding: "6px 0", borderTop: "1px solid var(--color-line)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 11 }}>
          {`Ahora: ${MODE_LABEL[current]}`}
          {capped && <span style={{ color: "var(--fg-mute)" }}>{` (corre en ${MODE_LABEL[control.ceiling].toLowerCase()} por el techo)`}</span>}
        </span>
      </div>
      {CAPABILITY_HINT[name] && <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>{CAPABILITY_HINT[name]}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
        {MODES.map((mode) => {
          const raising = rank(mode) > rank(current);
          const blocked = raising && (control.can[mode] ?? []).length > 0;
          return (
            <MacButton key={mode} sm ghost disabled={busy || mode === current || blocked} onClick={() => onPick(mode)}>
              {MODE_LABEL[mode]}
            </MacButton>
          );
        })}
      </div>
      {missing.length > 0 && next && (
        <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 4 }}>
          <div>{`Para ${MODE_LABEL[next].toLowerCase()} falta:`}</div>
          <ul style={{ margin: "2px 0 0", paddingLeft: 16 }}>
            {missing.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function WorkflowRow({
  control,
  busy,
  onPick,
}: {
  control: WorkflowControl;
  busy: boolean;
  onPick: (mode: WorkflowMode) => void;
}) {
  const current = control.mode;
  const blockedOn = (control.can.on ?? []).length > 0 && current !== "on";
  const missing = blockedOn ? failingDetails(control, "on") : [];
  return (
    <div role="group" aria-label="Workflow de ventas" style={{ padding: "6px 0", borderTop: "1px solid var(--color-line)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Workflow de ventas</span>
        <span style={{ fontSize: 11 }}>{`Ahora: ${WORKFLOW_LABEL[current]}`}</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
        <MacButton
          sm
          ghost
          disabled={busy || current === "canary" || (current === "off" && (control.can.canary ?? []).length > 0)}
          onClick={() => onPick("canary")}
        >
          V2 en canary
        </MacButton>
        <MacButton sm ghost disabled={busy || current === "on" || blockedOn} onClick={() => onPick("on")}>
          V2 para todos
        </MacButton>
        {current !== "off" && (
          <MacButton sm onClick={() => onPick("off")} disabled={busy} style={{ color: "var(--color-danger)" }}>
            Volver a V1
          </MacButton>
        )}
      </div>
      {missing.length > 0 && (
        <ul style={{ fontSize: 11, color: "var(--fg-mute)", margin: "4px 0 0", paddingLeft: 16 }}>
          {missing.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function confirmText(pending: Pending): { text: string; button: string } {
  if (pending.kind === "workflow") {
    return pending.mode === "on"
      ? { text: "Todas las conversaciones nuevas arrancarán en el workflow V2.", button: "Sí, V2 para todos" }
      : {
          text: "V2 atenderá los números de prueba y el porcentaje canary del bot nuevo; el resto sigue en V1.",
          button: "Sí, V2 en canary",
        };
  }
  const label = CAPABILITY_LABEL[pending.capability] ?? pending.capability;
  return pending.mode === "on"
    ? { text: `Jev decidirá «${label}» en todas las conversaciones (la regla queda de respaldo).`, button: "Sí, pasar a Jev" }
    : {
        text: `Jev decidirá «${label}» en los números de prueba y el porcentaje canary; el resto sigue en sombra.`,
        button: "Sí, pasar a canary",
      };
}

export function DecisionsControlPanel() {
  const { data, isLoading } = usePerceptionRollout();
  const setCapability = useSetCapabilityMode();
  const setWorkflow = useSetWorkflowMode();
  const [pending, setPending] = useState<Pending | null>(null);

  if (isLoading || !data) return null;
  const names = Object.keys(data.capabilities);
  if (names.length === 0 && data.workflow_v2.mode === "off" && data.workflow_v2.ceiling === "off") return null;

  const busy = setCapability.isPending || setWorkflow.isPending;

  const sendCapability = (capability: string, mode: PerceptionMode) => {
    setCapability.reset();
    setCapability.mutate({ capability, mode }, { onSuccess: () => setPending(null) });
  };
  const sendWorkflow = (mode: WorkflowMode) => {
    setWorkflow.reset();
    setWorkflow.mutate({ mode }, { onSuccess: () => setPending(null) });
  };

  const pickCapability = (capability: string, mode: PerceptionMode) => {
    if (rank(mode) >= rank("canary") && rank(mode) > rank(data.capabilities[capability]?.mode ?? "off")) {
      setPending({ kind: "capability", capability, mode });
    } else {
      sendCapability(capability, mode);
    }
  };
  const pickWorkflow = (mode: WorkflowMode) => {
    if (mode === "off") sendWorkflow(mode);
    else setPending({ kind: "workflow", mode });
  };

  const confirm = pending ? confirmText(pending) : null;
  const error = setCapability.isError ? setCapability.error : setWorkflow.isError ? setWorkflow.error : null;

  return (
    <Panel title="Motor de decisiones">
      <div style={{ fontSize: 11, color: "var(--fg-mute)", marginBottom: 4 }}>
        {`Techo de Terraform: capacidades ${Object.values(data.capabilities)[0]?.ceiling ?? "off"} · workflow ${data.workflow_v2.ceiling}`}
      </div>
      {names.map((name) => (
        <CapabilityRow
          key={name}
          name={name}
          control={data.capabilities[name]}
          busy={busy}
          onPick={(mode) => pickCapability(name, mode)}
        />
      ))}
      <WorkflowRow control={data.workflow_v2} busy={busy} onPick={pickWorkflow} />

      {pending && confirm && (
        <div role="group" aria-label="Confirmar el cambio" style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
          <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>{confirm.text}</p>
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm onClick={() => setPending(null)}>
              Volver
            </MacButton>
            <MacButton
              primary
              sm
              disabled={busy}
              onClick={() =>
                pending.kind === "workflow" ? sendWorkflow(pending.mode) : sendCapability(pending.capability, pending.mode)
              }
            >
              {confirm.button}
            </MacButton>
          </div>
        </div>
      )}

      {error !== null && (
        <div role="alert" style={{ fontSize: 11, color: "var(--color-danger)", marginTop: 8 }}>
          <div>No se hizo el cambio:</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
            {errorLines(error).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}
