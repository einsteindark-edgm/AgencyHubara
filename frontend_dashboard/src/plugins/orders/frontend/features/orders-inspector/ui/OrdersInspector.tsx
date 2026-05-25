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

import {
  ORDER_STATUS_META,
  PAY_STATUS_META,
  useOrderDetail,
  type Order,
  type OrderItemDetail,
  type OrderDetail,
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
        <ReadyForShip order={order} />

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
            <TimelinePanel detail={detail} missing={missing} />
            <ItemsPanel detail={detail} />
            <DeliveryPanel detail={detail} missing={missing} order={order} />
            <PaymentPanel detail={detail} missing={missing} order={order} />
            <NotesPanel missing={missing} />
            <CustomerHistoryPanel missing={missing} />
          </>
        )}
      </div>
    </aside>
  );
}

/* ── Panels ──────────────────────────────────────────────────────────── */

function TimelinePanel({
  detail,
  missing,
}: {
  detail: OrderDetail;
  missing: Set<string>;
}) {
  const events = detail.timeline;
  return (
    <InsBlock title={`Línea de tiempo (${events.length})`}>
      <div className="ord-tl">
        {events.map((e, i) => (
          <Timeline
            key={`${e.type}-${i}`}
            t={e.label}
            sub={`${new Date(e.timestamp_ms).toLocaleDateString("es-CO", {
              day: "2-digit",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}${e.detail ? ` · ${e.detail}` : ""}`}
            done
          />
        ))}
        {events.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--fg-muted)", padding: 8 }}>
            Aún no hay eventos en el timeline.
          </div>
        )}
        {(missing.has("tracking_number") || missing.has("shipping_provider")) && (
          <div style={{ marginTop: 8 }}>
            <MissingData reason="Tracking + transportadora — pendiente integrar con shipping providers." />
          </div>
        )}
      </div>
    </InsBlock>
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
        k="Fecha estimada"
        v={
          missing.has("due_date") ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {order.dueIso || "—"}
              <MissingData
                label="Estimada"
                reason="Hubara aún no tiene tracking de fecha de compromiso de entrega — esta fecha es created_at + 1 día. Cuando integremos shipping providers se reemplaza con la real."
              />
            </span>
          ) : (
            order.dueIso || "—"
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
  return (
    <InsBlock title="Pago" open={false}>
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
      <KV k="Tipo" v={order.payType === "cod" ? "Contra entrega" : "Pago confirmado"} />
      <KV k="Total" v={fmtMoney(order.total)} />
      {missing.has("payment_method_detail") && (
        <KV
          k="Detalle"
          v={
            <MissingData reason="Detalle del cargo (últimos dígitos, comisión gateway) — pendiente integrar con gateway Wompi." />
          }
        />
      )}
    </InsBlock>
  );
}

function NotesPanel({ missing }: { missing: Set<string> }) {
  if (!missing.has("notes")) return null;
  return (
    <InsBlock title="Notas internas" open={false}>
      <MissingData
        variant="block"
        label="Sin notas internas"
        reason="Aún no implementamos persistencia de notas internas por orden — pendiente desarrollo."
      />
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

function Timeline({
  t,
  sub,
  done,
  cur,
}: {
  t: string;
  sub: string;
  done?: boolean;
  cur?: boolean;
}) {
  return (
    <div className={"tl-row" + (done ? " done" : "") + (cur ? " cur" : "")}>
      <div className="tl-dot">{done && <Icon.check />}</div>
      <div className="tl-t">{t}</div>
      <div className="tl-s">{sub}</div>
    </div>
  );
}

function ItemRow({ item }: { item: OrderItemDetail }) {
  const labelExtra = item.variant_label ? ` (${item.variant_label})` : "";
  return (
    <div className="item-row">
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
      <div className="ir-b">
        <div className="ir-n">
          {item.title}
          {labelExtra}
        </div>
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
