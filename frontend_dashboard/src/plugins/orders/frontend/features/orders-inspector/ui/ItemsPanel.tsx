import type {
  OrderDetail,
  OrderItemDetail,
} from "@plugins/orders/frontend/entities/order";
import { fmtMoney } from "@/shared/lib";
import { Icon, InsBlock } from "@/shared/ui";
import { KV } from "./KV";

export function ItemsPanel({ detail }: { detail: OrderDetail }) {
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
                ? "var(--color-warn)"  // ámbar warning
                : "var(--fg-soft)",
            }}
          >
            <span
              style={{
                padding: "1px 6px",
                background: item.variant_label_mismatch
                  ? "rgba(255,180,74,0.12)"
                  : "rgba(214,138,255,0.12)",
                color: item.variant_label_mismatch ? "var(--color-warn)" : "var(--color-violet)",
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
              color: "var(--color-warn)",
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
