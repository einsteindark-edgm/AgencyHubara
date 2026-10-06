/**
 * Motor de decisiones — diseño v2 §08 y §11, fase F7.
 *
 * Cada capacidad (una pieza de código quemado que ahora decide el motor) con
 * su modo: reglas (la de hoy) → sombra (Jev al lado, sin actuar) → canary
 * (números de prueba y porcentaje) → Jev. Y la versión del workflow de
 * ventas: V2 por canary antes de todos. Todo dentro de los techos de
 * Terraform.
 *
 * SOLO LECTURA desde el 2026-10-06 (decisión del operador: «para evitar que
 * alguien jugando dañe producción»): dice qué corre y qué falta para subir;
 * se cambia por comando (`decisions/control.py`).
 */

import {
  usePerceptionRollout,
  type CapabilityControl,
  type PerceptionMode,
  type WorkflowControl,
  type WorkflowMode,
} from "@plugins/agents_admin/frontend/entities/perception-rollout";
import { Panel } from "@/shared/ui";

import { ByCommandNote } from "./ByCommandNote";

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
  acuse: "Acuse tras la despedida",
  cortesia: "Cortesía sin venta",
  cupon: "Cupón",
  fuera_de_catalogo: "Fuera de catálogo",
  cantidad: "Cantidad",
  categoria: "Categoría",
  familia_de_color: "Familia de color",
  item_del_pedido: "Ítem del pedido",
  zona_de_envio: "Zona de envío",
  datos: "Datos de envío",
  producto_nombrado: "Producto nombrado",
  persona: "Persona",
  enumeracion: "Enumeración",
  monto: "Monto",
  selector: "Selector",
  contactar: "Contactar",
  cierre: "Cierre por abandono",
  relevo: "Colega prometido",
  afirmacion: "Afirmación",
  preambulo: "Preámbulo del modelo",
  destinatario: "Destinatario",
  rescate: "Rescate",
  portavelas: "Portavelas",
  saludo: "Saludo",
};

const CAPABILITY_HINT: Record<string, string> = {
  compra: "¿el cliente confirma la compra?",
  retoma: "¿aplazó la conversación y hasta cuándo?",
  baja: "¿pide no recibir más mensajes?",
  acuse: "¿el cliente solo agradece o se despide, sin pedir nada?",
  cortesia: "¿el cliente solo agradece o saluda, sin pedir nada?",
  cupon: "¿habla de un cupón o una promoción?",
  fuera_de_catalogo: "¿pide algo que no vendemos?",
  cantidad: "¿cuántas unidades dijo?",
  categoria: "¿qué categoría busca?",
  familia_de_color: "¿a qué familia pertenece el color?",
  item_del_pedido: "¿a qué producto del pedido va el dato?",
  zona_de_envio: "¿la ciudad paga tarifa de Bogotá o nacional?",
  datos: "¿el cliente dio el dato que se va a guardar?",
  producto_nombrado: "¿de qué producto habla el cliente? (remarketing)",
  persona: "¿el texto delata que atiende un bot?",
  enumeracion: "¿el texto lista variantes para el selector?",
  monto: "¿la oración cotiza el precio de un producto?",
  selector: "¿los botones eligen producto o variante?",
  contactar: "¿sobra el mensaje proactivo de remarketing?",
  cierre: "¿cómo quedó la conversación que el cliente dejó? (V2)",
  relevo: "¿el mensaje promete que un colega lo atiende? (escala si nadie lo hizo)",
  afirmacion: "¿afirma algo sin consultarlo? (solo se mide)",
  preambulo: "¿la oración es una muletilla del modelo («Aquí tienes:»)?",
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

function rank(mode: string): number {
  return MODES.indexOf(mode as PerceptionMode);
}

function failingDetails(control: CapabilityControl | WorkflowControl, target: string): string[] {
  return (control.readiness[target] ?? []).filter((c) => !c.ok).map((c) => c.detail || c.code);
}

function CapabilityRow({ name, control }: { name: string; control: CapabilityControl }) {
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

function WorkflowRow({ control }: { control: WorkflowControl }) {
  const current = control.mode;
  const blockedOn = (control.can.on ?? []).length > 0 && current !== "on";
  const missing = blockedOn ? failingDetails(control, "on") : [];
  return (
    <div role="group" aria-label="Workflow de ventas" style={{ padding: "6px 0", borderTop: "1px solid var(--color-line)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Workflow de ventas</span>
        <span style={{ fontSize: 11 }}>{`Ahora: ${WORKFLOW_LABEL[current]}`}</span>
      </div>
      {missing.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 4 }}>
          <div>Para V2 en todos falta:</div>
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

export function DecisionsControlPanel() {
  const { data, isLoading } = usePerceptionRollout();

  if (isLoading || !data) return null;
  const names = Object.keys(data.capabilities);
  if (names.length === 0 && data.workflow_v2.mode === "off" && data.workflow_v2.ceiling === "off") return null;

  return (
    <Panel title="Motor de decisiones">
      <div style={{ fontSize: 11, color: "var(--fg-mute)", marginBottom: 4 }}>
        {`Techo de Terraform: capacidades ${Object.values(data.capabilities)[0]?.ceiling ?? "off"} · workflow ${data.workflow_v2.ceiling}`}
      </div>
      {names.map((name) => (
        <CapabilityRow key={name} name={name} control={data.capabilities[name]} />
      ))}
      <WorkflowRow control={data.workflow_v2} />
      <ByCommandNote example="--por <quien> capacidad <nombre|todas> <modo>  ·  workflow <off|canary|on>" />
    </Panel>
  );
}
