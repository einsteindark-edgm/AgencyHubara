/**
 * Inspector de Órdenes (panel derecho). Top: header con ID/cliente/pills/
 * acciones. Body: ReadyForShip + paneles colapsables (Línea de tiempo,
 * Productos, Entrega, Pago, Notas, Historial cliente).
 *
 * Datos reales: viene de `useOrderDetail(displayId)` que consulta el
 * backend `/api/orders/orders/{id}` (Medusa v2 vía MedusaOrderQuery).
 *
 * Datos que Medusa NO tiene todavía (timeline detallado, notas internas,
 * historial cliente, agente asignado) se renderizan con el marker
 * `MissingData` para que el operador vea explícitamente qué falta integrar.
 */

import { useMemo, useState } from "react";
import {
  ORDER_STATUS_META,
  PAY_STATUS_META,
  useCancelOrder,
  useConfirmOrderPayment,
  useOrderDetail,
  type Order,
  type OrderItemDetail,
  type OrderDetail,
  type OrderStatus,
} from "@/entities/order";
import { fmtMoney } from "@/shared/lib";
import { Icon, InsBlock, MacButton, MissingData } from "@/shared/ui";
import { ReadyForShip } from "./ReadyForShip";

interface Props {
  order: Order | null;
}

export function OrdersInspector({ order }: Props) {
  // Llamar el hook SIEMPRE (no condicional) — pasa null cuando no hay
  // selección y el hook lo desactiva internamente. Cumple Rules of Hooks.
  const detailQuery = useOrderDetail(order?.id ?? null);

  if (!order) {
    return (
      <aside
        className="inspector"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--fg-muted)",
          padding: 24,
          textAlign: "center",
        }}
      >
        <p style={{ fontSize: 12 }}>Selecciona una orden para ver sus detalles</p>
      </aside>
    );
  }

  const statusMeta = ORDER_STATUS_META[order.status];
  const detail = detailQuery.data;
  const missing = new Set(detail?.data_completeness_missing ?? []);

  return (
    <aside className="inspector">
      <div className="ins-head">
        <div className="ih-title">
          <h3>
            {order.id}
            {order.isDraft && (
              <span
                style={{
                  marginLeft: 8,
                  fontSize: 9,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: "rgba(214,138,255,0.18)",
                  color: "#d68aff",
                  verticalAlign: "middle",
                }}
              >
                Draft
              </span>
            )}
          </h3>
          <p>{order.customer}</p>
        </div>
        <div className="oi-pills">
          <span
            className="ord-pill"
            style={{ background: statusMeta.bg, color: statusMeta.color }}
          >
            <span className="d" style={{ background: statusMeta.color }} />
            {statusMeta.label}
          </span>
          <span
            className="ord-pill"
            style={{
              background: "rgba(255,255,255,0.06)",
              color: "var(--fg-soft)",
            }}
          >
            {order.channel}
          </span>
        </div>
        <div className="oi-actions">
          <MacButton primary sm style={{ flex: 1 }}>
            Avanzar estado
          </MacButton>
          <MacButton ghost sm>
            <Icon.msg />
          </MacButton>
          <MacButton ghost sm>
            <Icon.phone />
          </MacButton>
          <MacButton ghost sm>
            <Icon.more />
          </MacButton>
        </div>
      </div>

      <div className="ins-body">
        {/* "Lista para envío" — único panel de agendamiento. Solo aparece
            para orders nuevas sin fecha asignada (acción primaria del
            operador antes de avanzar al kanban). Una vez agendada,
            desaparece. */}
        {order.status === "new" && !order.dueIso && (
          <ReadyForShip order={order} />
        )}

        {detailQuery.isLoading && (
          <div style={{ padding: 16, color: "var(--fg-muted)", fontSize: 12 }}>
            Cargando detalle…
          </div>
        )}

        {detailQuery.isError && (
          <div style={{ padding: 16 }}>
            <MissingData
              variant="block"
              label="No se pudo cargar el detalle"
              reason="Medusa no respondió. Recarga la página o verifica que el backend esté arriba."
            />
          </div>
        )}

        {detail && (
          <>
            <TimelinePanel detail={detail} order={order} missing={missing} />
            <ItemsPanel detail={detail} />
            <DeliveryPanel detail={detail} missing={missing} order={order} />
            <PaymentPanel detail={detail} missing={missing} order={order} />
            <NotesPanel detail={detail} />
            <DangerPanel order={order} />
            <CustomerHistoryPanel missing={missing} />
          </>
        )}
      </div>
    </aside>
  );
}

/* ── Panels ──────────────────────────────────────────────────────────── */

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
function TimelinePanel({
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

function ItemsPanel({ detail }: { detail: OrderDetail }) {
  const items = detail.items_detail;
  return (
    <InsBlock title={`Productos (${items.length})`}>
      {items.map((it, i) => (
        <ItemRow key={`${it.sku}-${i}`} item={it} />
      ))}
      <div style={{ marginTop: 8 }}>
        <KV k="Subtotal" v={fmtMoney(detail.subtotal_cop)} />
        {detail.shipping_cop > 0 && (
          <KV k="Envío" v={fmtMoney(detail.shipping_cop)} />
        )}
        {detail.discount_total_cop > 0 && (
          <KV k="Descuento" v={"− " + fmtMoney(detail.discount_total_cop)} />
        )}
        {detail.tax_total_cop > 0 && (
          <KV k="IVA" v={fmtMoney(detail.tax_total_cop)} />
        )}
        <div className="kv tot">
          <span className="k">Total</span>
          <span className="v">{fmtMoney(detail.summary.total_cop)}</span>
        </div>
      </div>
    </InsBlock>
  );
}

function DeliveryPanel({
  detail,
  missing,
  order,
}: {
  detail: OrderDetail;
  missing: Set<string>;
  order: Order;
}) {
  const addr = detail.shipping_address;
  return (
    <InsBlock title="Entrega" open>
      {addr ? (
        <>
          <KV
            k="Destinatario"
            v={`${addr.first_name ?? "—"} ${addr.last_name ?? ""}`.trim() || "—"}
          />
          <KV k="Dirección" v={addr.address_1 ?? "—"} />
          {addr.address_2 && <KV k="Barrio" v={addr.address_2} />}
          <KV k="Ciudad" v={addr.city ?? "—"} />
          <KV k="Teléfono" v={addr.phone ?? "—"} />
        </>
      ) : (
        <div style={{ fontSize: 11, color: "var(--fg-muted)", padding: 8 }}>
          Sin dirección de envío.
        </div>
      )}
      <KV
        k="Fecha de entrega"
        v={
          order.dueIso ? (
            <span>
              {order.dueIso}
              {order.dueTime && order.dueTime !== "—" ? ` · ${order.dueTime}` : ""}
            </span>
          ) : (
            <MissingData
              label="Sin agendar"
              reason="No se le ha asignado fecha de entrega al pedido. Usá el panel 'Lista para envío' para asignar."
            />
          )
        }
      />
      {missing.has("tracking_number") && (
        <KV
          k="Guía"
          v={
            <MissingData
              label="Pendiente"
              reason="Tracking number — pendiente integrar con shipping provider."
            />
          }
        />
      )}
      {missing.has("shipping_provider") && (
        <KV
          k="Transportadora"
          v={<MissingData reason="Pendiente integrar shipping providers (Coordinadora, Envia, etc.)." />}
        />
      )}
    </InsBlock>
  );
}

function PaymentPanel({
  detail,
  missing,
  order,
}: {
  detail: OrderDetail;
  missing: Set<string>;
  order: Order;
}) {
  const confirmPayment = useConfirmOrderPayment();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const isPaid = order.payStatus === "paid";
  const onConfirm = () => {
    setErrorMsg(null);
    confirmPayment.mutate(
      { orderId: order.id },
      {
        onSuccess: (r) => {
          if (!r.success && r.error_detail) setErrorMsg(r.error_detail);
        },
        onError: (err) => setErrorMsg(err.message),
      },
    );
  };

  return (
    <InsBlock title="Pago" open>
      <KV
        k="Estado"
        v={
          <span style={{ color: PAY_STATUS_META[order.payStatus].color }}>
            ● {PAY_STATUS_META[order.payStatus].label}
          </span>
        }
      />
      <KV
        k="Método"
        v={
          detail.payment_method_label ?? (
            <MissingData label="Sin información" reason="Medusa no devolvió el método de pago — verifica metadata del Draft Order." />
          )
        }
      />
      {/* "Modalidad" = cómo se paga (anticipado vs contra entrega). NO es
          el estado del pago — eso vive en el KV "Estado" de arriba. */}
      <KV k="Modalidad" v={order.payType === "cod" ? "Contra entrega" : "Anticipado"} />
      <KV k="Total" v={fmtMoney(order.total)} />
      {missing.has("payment_method_detail") && (
        <KV
          k="Detalle"
          v={
            <MissingData reason="Detalle del cargo (últimos dígitos, comisión gateway) — pendiente integrar con gateway Wompi." />
          }
        />
      )}
      {!isPaid && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
          <MacButton
            primary
            sm
            onClick={onConfirm}
            disabled={confirmPayment.isPending}
          >
            {confirmPayment.isPending ? "Confirmando…" : "✓ Confirmar pago"}
          </MacButton>
          <p style={{ fontSize: 10, color: "var(--fg-muted)", margin: 0 }}>
            Marca este pedido como pagado en metadata. Hoy NO toca el
            payment_status de Medusa — cuando integremos gateway, también
            capturará el pago real.
          </p>
        </div>
      )}
      {errorMsg && (
        <div
          style={{
            marginTop: 8,
            padding: 8,
            background: "rgba(255,114,105,0.12)",
            border: "1px solid rgba(255,114,105,0.3)",
            color: "#ff7269",
            fontSize: 11,
            borderRadius: 4,
          }}
        >
          {errorMsg}
        </div>
      )}
    </InsBlock>
  );
}

function NotesPanel({ detail }: { detail: OrderDetail }) {
  // Mostramos las notas reales (human_note + cancelled_reason) si hay.
  // Si no hay, mostramos placeholder más conservador — la operación de
  // agregar notas se hace via el SchedulePanel (cuando el humano agenda).
  if (detail.notes.length === 0) {
    return (
      <InsBlock title="Notas" open={false}>
        <div
          style={{ fontSize: 11, color: "var(--fg-muted)", padding: 8 }}
        >
          Sin notas. Agendá el pedido para añadir una nota visible al equipo.
        </div>
      </InsBlock>
    );
  }
  return (
    <InsBlock title={`Notas (${detail.notes.length})`} open>
      {detail.notes.map((note, i) => (
        <div
          key={i}
          style={{
            padding: "6px 8px",
            background: "rgba(255,255,255,0.03)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--fg-soft)",
            marginBottom: 4,
            whiteSpace: "pre-wrap",
          }}
        >
          {note}
        </div>
      ))}
    </InsBlock>
  );
}

/* SchedulePanel removed (2026-05-26) — merged into `ReadyForShip` as the
   single, canonical scheduling form. Wiring lives in `./ReadyForShip.tsx`. */

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: "6px 8px",
  background: "rgba(255,255,255,0.04)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 4,
  color: "var(--fg)",
  fontSize: 12,
};

/* ── DangerPanel — cancelar la orden con confirmación ─────────────────── */

function DangerPanel({ order }: { order: Order }) {
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const cancel = useCancelOrder();

  // Si ya está cancelada, no mostramos el panel.
  if (order.status === "cancelled") return null;

  const onCancel = () => {
    setErrorMsg(null);
    cancel.mutate(
      { orderId: order.id, reason: reason || undefined },
      {
        onSuccess: (r) => {
          if (!r.success && r.error_detail) setErrorMsg(r.error_detail);
          else setConfirming(false);
        },
        onError: (err) => setErrorMsg(err.message),
      },
    );
  };

  return (
    <InsBlock title="Cancelar pedido" open={false}>
      {!confirming ? (
        <MacButton
          ghost
          sm
          onClick={() => setConfirming(true)}
          style={{ color: "#ff7269" }}
        >
          Cancelar pedido…
        </MacButton>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>
            ¿Seguro? Esta acción marca la orden como <b>Cancelada</b>. Si ya
            está en preparación o en camino, considerá usar la nota para
            explicar.
          </p>
          <input
            placeholder="Motivo (opcional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={inputStyle}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton
              ghost
              sm
              onClick={() => {
                setConfirming(false);
                setReason("");
                setErrorMsg(null);
              }}
            >
              Volver
            </MacButton>
            <MacButton
              primary
              sm
              onClick={onCancel}
              disabled={cancel.isPending}
              style={{ background: "#ff7269", borderColor: "#ff7269" }}
            >
              {cancel.isPending ? "Cancelando…" : "Sí, cancelar"}
            </MacButton>
          </div>
          {errorMsg && (
            <div
              style={{
                padding: 8,
                background: "rgba(255,114,105,0.12)",
                border: "1px solid rgba(255,114,105,0.3)",
                color: "#ff7269",
                fontSize: 11,
                borderRadius: 4,
              }}
            >
              {errorMsg}
            </div>
          )}
        </div>
      )}
    </InsBlock>
  );
}

function CustomerHistoryPanel({ missing }: { missing: Set<string> }) {
  if (!missing.has("customer_history")) return null;
  return (
    <InsBlock title="Historial cliente" open={false}>
      <MissingData
        variant="block"
        label="Sin historial de cliente"
        reason="Aún no agregamos métricas de cliente (LTV, órdenes totales, valor total, tag VIP) — pendiente desarrollo."
      />
    </InsBlock>
  );
}

/* ── Helpers ─────────────────────────────────────────────────────────── */
/* Old `Timeline` helper removed — `TimelineRow` (above) replaces it with
   styling for done/cur/pending states. */

function ItemRow({ item }: { item: OrderItemDetail }) {
  return (
    <div className="item-row" style={{ alignItems: "flex-start" }}>
      <div className="ir-thumb">
        {item.thumbnail ? (
          <img
            src={item.thumbnail}
            alt={item.title}
            style={{ width: 28, height: 28, objectFit: "cover", borderRadius: 4 }}
          />
        ) : (
          <Icon.pkg />
        )}
      </div>
      <div className="ir-b" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div className="ir-n">{item.title}</div>
        {item.variant_label && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11,
              color: item.variant_label_mismatch
                ? "#ffb44a"  // ámbar warning
                : "var(--fg-soft)",
            }}
          >
            <span
              style={{
                padding: "1px 6px",
                background: item.variant_label_mismatch
                  ? "rgba(255,180,74,0.12)"
                  : "rgba(214,138,255,0.12)",
                color: item.variant_label_mismatch ? "#ffb44a" : "#d68aff",
                borderRadius: 3,
                fontWeight: 600,
                letterSpacing: 0.3,
                textTransform: "uppercase",
                fontSize: 9,
              }}
            >
              {item.variant_label_mismatch ? "⚠ Variante" : "Variante"}
            </span>
            <span style={{ fontWeight: 500 }}>{item.variant_label}</span>
          </div>
        )}
        {item.variant_label_mismatch && (
          <div
            style={{
              fontSize: 10,
              color: "#ffb44a",
              marginTop: 2,
              lineHeight: 1.35,
            }}
          >
            La variante que pidió el cliente NO matchea con ninguna variante
            real del producto en Medusa. Verificá manualmente antes de despachar
            (el LLM puede haber registrado la primera variante por defecto).
          </div>
        )}
        <div className="ir-s">
          {item.sku ?? "—"} · {item.quantity} und × {fmtMoney(item.unit_price_cop)}
        </div>
      </div>
      <div className="ir-t">{fmtMoney(item.total_cop)}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
