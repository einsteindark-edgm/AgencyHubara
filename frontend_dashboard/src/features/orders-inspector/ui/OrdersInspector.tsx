/**
 * Inspector de Órdenes (panel derecho). Top: header con ID/cliente/pills/
 * acciones. Body: ReadyForShip + paneles colapsables (Línea de tiempo,
 * Productos, Entrega, Pago, Notas, Historial cliente).
 */

import {
  ORDER_STATUS_META,
  PAY_STATUS_META,
  type Order,
} from "@/entities/order";
import { fmtMoney } from "@/shared/lib";
import { Icon, InsBlock, MacButton } from "@/shared/ui";
import { ReadyForShip } from "./ReadyForShip";

interface Props {
  order: Order | null;
}

export function OrdersInspector({ order }: Props) {
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

  return (
    <aside className="inspector">
      <div className="ins-head">
        <div className="ih-title">
          <h3>{order.id}</h3>
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

        <InsBlock title="Línea de tiempo">
          <div className="ord-tl">
            <Timeline t="Orden creada"     sub="11 may · 18:42 · WhatsApp" done />
            <Timeline t="Pago confirmado"  sub="11 may · 19:01 · Wompi"     done />
            <Timeline t="En preparación"   sub="12 may · 08:15 · Sofía"     done />
            <Timeline t="Lista para envío" sub="Estimado: hoy · 11:30"      cur />
            <Timeline t="En camino"        sub="—" />
            <Timeline t="Entregada"        sub="Estimado: hoy · 14:00" />
          </div>
        </InsBlock>

        <InsBlock title="Productos (3)">
          <ItemRow name="Vela Sagrado Rostro" sku="VLA-001" qty={2} price={36000} />
          <ItemRow name="Vela Ángel Guardián" sku="VLA-002" qty={4} price={42000} />
          <ItemRow name="Vela Inmaculada"     sku="VLA-003" qty={2} price={29990} />
          <div style={{ marginTop: 8 }}>
            <KV k="Subtotal"  v={fmtMoney(216000)} />
            <KV k="Envío"     v={fmtMoney(12000)} />
            <KV k="Descuento" v={"− " + fmtMoney(6000)} />
            <KV k="IVA (19%)" v={fmtMoney(40299)} />
            <div className="kv tot">
              <span className="k">Total</span>
              <span className="v">{fmtMoney(order.total)}</span>
            </div>
          </div>
        </InsBlock>

        <InsBlock title="Entrega" open>
          <KV k="Fecha" v="Hoy 12 may · 09:30" />
          <KV k="Ventana" v="9:00 — 11:00" />
          <KV k="Dirección" v="Cra 13 #94-30, Bogotá" />
          <KV k="Transportadora" v="Coordinadora · 215XXX" />
          <KV k="Guía" v="900112334" />
          <div className="map-mini">
            <div className="map-grid-bg" />
            <span className="map-pin"><Icon.loc /></span>
            <span className="map-lbl">Bogotá · Chapinero</span>
          </div>
        </InsBlock>

        <InsBlock title="Pago" open={false}>
          <KV
            k="Estado"
            v={
              <span style={{ color: PAY_STATUS_META[order.payStatus].color }}>
                ● {PAY_STATUS_META[order.payStatus].label}
              </span>
            }
          />
          <KV k="Método" v="Tarjeta · Visa •• 4421" />
          <KV k="Pagado" v={fmtMoney(order.total)} />
          <KV k="Comisión" v={"− " + fmtMoney(Math.floor(order.total * 0.029))} />
          <KV k="Neto" v={fmtMoney(Math.floor(order.total * 0.971))} />
        </InsBlock>

        <InsBlock title="Notas internas" open={false}>
          <div className="note">
            <div className="nm">Sofía · hace 1 h</div>
            <div className="nb">
              Cliente pidió empaque para regalo con tarjeta. Color crema.
            </div>
          </div>
          <div className="note">
            <div className="nm">Diego · hace 3 h</div>
            <div className="nb">
              Confirmar dirección por WhatsApp antes de despachar.
            </div>
          </div>
          <MacButton ghost sm style={{ width: "100%" }}>
            + Agregar nota
          </MacButton>
        </InsBlock>

        <InsBlock title="Historial cliente" open={false}>
          <KV k="Órdenes totales" v="14" />
          <KV k="Valor total" v={fmtMoney(1248500)} />
          <KV k="Última compra" v="hace 22 días" />
          <KV k="Tag" v="VIP · Recurrente" />
          <KV k="Score" v="A · Alta probabilidad recompra" />
        </InsBlock>
      </div>
    </aside>
  );
}

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

function ItemRow({
  name,
  sku,
  qty,
  price,
}: {
  name: string;
  sku: string;
  qty: number;
  price: number;
}) {
  return (
    <div className="item-row">
      <div className="ir-thumb"><Icon.pkg /></div>
      <div className="ir-b">
        <div className="ir-n">{name}</div>
        <div className="ir-s">
          {sku} · {qty} und × {fmtMoney(price)}
        </div>
      </div>
      <div className="ir-t">{fmtMoney(qty * price)}</div>
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
