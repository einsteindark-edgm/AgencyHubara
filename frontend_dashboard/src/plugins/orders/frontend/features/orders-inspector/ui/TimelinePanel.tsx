import { useMemo } from "react";
import type {
  Order,
  OrderDetail,
  OrderStatus,
} from "@plugins/orders/frontend/entities/order";
import { Icon, InsBlock, MissingData } from "@/shared/ui";

/**
 * Timeline panel — stepper completo del ciclo de vida del pedido.
 *
 * Muestra todos los pasos esperados (Orden creada → [Pago confirmado] →
 * En preparación → Lista para envío → En camino → Entregada) con styling
 * `done`/`cur`/`pending` según el estado real. Si la orden fue cancelada,
 * cuelga "Cancelada" al final con los stages previos marcados según los
 * eventos reales.
 *
 * Source of truth: `detail.timeline` (eventos reales del history) +
 * `order.status` (stage actual). Los stages futuros se infieren.
 */
export function TimelinePanel({
  detail,
  order,
  missing,
}: {
  detail: OrderDetail;
  order: Order;
  missing: Set<string>;
}) {
  const steps = useMemo(
    () => buildTimelineSteps(detail.timeline, order),
    [detail.timeline, order],
  );

  return (
    <InsBlock title="Línea de tiempo" open>
      <div className="ord-tl">
        {steps.map((s) => (
          <TimelineRow key={s.key} step={s} />
        ))}
        {(missing.has("tracking_number") || missing.has("shipping_provider")) && (
          <div style={{ marginTop: 8 }}>
            <MissingData reason="Tracking + transportadora — pendiente integrar con shipping providers." />
          </div>
        )}
      </div>
    </InsBlock>
  );
}

type StepState = "done" | "cur" | "pending";
interface TimelineStep {
  key: string;
  label: string;
  state: StepState;
  timestamp_ms: number | null;
  detail: string | null;
}

/**
 * Genera la lista de pasos del timeline a partir de los eventos reales del
 * history + el stage actual. Reglas:
 *
 *   * Paso "Orden creada" siempre presente (timestamp = created_at_ms).
 *   * Paso "Pago confirmado" solo si hay evento `payment_confirmed` real.
 *     Es ortogonal al stage operacional (independiente del kanban column).
 *   * Stages "En preparación", "Lista para envío", "En camino", "Entregada"
 *     son la pipeline canónica. `done` si el stage actual está más adelante,
 *     `cur` si es el actual, `pending` si está más atrás.
 *   * Si el stage actual es "cancelled": los pasos previos quedan `done`
 *     si tienen evento real, `pending` si no. Al final agregamos
 *     "Cancelada" con `state="done"`.
 */
function buildTimelineSteps(
  events: OrderDetail["timeline"],
  order: Order,
): TimelineStep[] {
  // Index events by type for O(1) lookup.
  const evByType = new Map<string, OrderDetail["timeline"][number]>();
  for (const e of events) evByType.set(e.type, e);

  const isCancelled = order.status === "cancelled";

  // Pipeline canónica de stages operacionales (excluye "new" porque ese
  // se cubre con "Orden creada", y excluye "cancelled" porque es branch).
  const pipeline: { stage: OrderStatus; label: string; eventType: string }[] = [
    { stage: "preparing", label: "En preparación", eventType: "stage:preparing" },
    { stage: "ready",     label: "Lista para envío", eventType: "stage:ready" },
    { stage: "shipping",  label: "En camino",     eventType: "stage:shipping" },
    { stage: "delivered", label: "Entregada",     eventType: "stage:delivered" },
  ];

  // Cálculo de "qué tan lejos llegó la orden". Si isCancelled, no hay
  // currentIdx — solo marcamos como done lo que tenga evento real.
  const stageOrder: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered"];
  const currentIdx = stageOrder.indexOf(order.status);

  const steps: TimelineStep[] = [];

  // 1. Orden creada — siempre presente.
  steps.push({
    key: "created",
    label: "Orden creada",
    state: "done",
    timestamp_ms:
      evByType.get("created")?.timestamp_ms ??
      // Si no hay evento "created" explícito, intentamos el primer entry
      // del history que apunta a stage `new` (from=null to=new).
      events.find((e) => e.type === "stage:new")?.timestamp_ms ??
      // Fallback final: el `summary.created_at_ms` si está expuesto.
      // El detail no incluye created_at_ms directo — usamos updated_at_ms
      // como aprox del primer evento. Es OK: si llega acá significa que
      // el history está incompleto.
      events[0]?.timestamp_ms ??
      null,
    detail: null,
  });

  // 2. Pago confirmado — solo si hay evento.
  const payment = evByType.get("payment_confirmed");
  if (payment) {
    steps.push({
      key: "payment_confirmed",
      label: "Pago confirmado",
      state: "done",
      timestamp_ms: payment.timestamp_ms,
      detail: payment.detail,
    });
  }

  // 3. Pipeline operacional.
  for (const p of pipeline) {
    const ev = evByType.get(p.eventType);
    let state: StepState;
    if (isCancelled) {
      state = ev ? "done" : "pending";
    } else {
      const stepIdx = stageOrder.indexOf(p.stage);
      if (stepIdx < currentIdx) state = "done";
      else if (stepIdx === currentIdx) state = "cur";
      else state = "pending";
    }
    steps.push({
      key: p.stage,
      label: p.label,
      state,
      timestamp_ms: ev?.timestamp_ms ?? null,
      detail: ev?.detail ?? null,
    });
  }

  // 4. Si cancelled, agregamos el paso "Cancelada" al final.
  if (isCancelled) {
    const cancelEv = evByType.get("stage:cancelled");
    steps.push({
      key: "cancelled",
      label: "Cancelada",
      state: "done",
      timestamp_ms: cancelEv?.timestamp_ms ?? null,
      detail: cancelEv?.detail ?? null,
    });
  }

  return steps;
}

function TimelineRow({ step }: { step: TimelineStep }) {
  const isDone = step.state === "done";
  const isCur = step.state === "cur";
  const dateLabel = step.timestamp_ms
    ? new Date(step.timestamp_ms).toLocaleDateString("es-CO", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : step.state === "pending"
      ? "Pendiente"
      : "—";
  const sub = step.detail
    ? `${dateLabel} · ${step.detail}`
    : dateLabel;
  return (
    <div
      className={"tl-row" + (isDone ? " done" : "") + (isCur ? " cur" : "")}
      style={
        step.state === "pending"
          ? { opacity: 0.45 }
          : isCur
            ? { fontWeight: 600 }
            : undefined
      }
    >
      <div className="tl-dot">
        {isDone && <Icon.check />}
        {isCur && <span style={{ width: 6, height: 6, borderRadius: 6, background: "currentColor", display: "inline-block" }} />}
      </div>
      <div className="tl-t">{step.label}</div>
      <div className="tl-s">{sub}</div>
    </div>
  );
}

/* Old `Timeline` helper removed — `TimelineRow` (above) replaces it with
   styling for done/cur/pending states. */
