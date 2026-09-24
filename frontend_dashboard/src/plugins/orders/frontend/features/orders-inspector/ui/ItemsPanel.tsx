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
      {/* Desglose: subtotal = SOLO productos (el backend ya no usa el
          `subtotal` de Medusa, que trae el envío adentro), envío aparte —
          estimado (tarifa mínima del registro) hasta que el operador fija el
          real al marcar "en camino" — y total = subtotal + envío. */}
      <div style={{ marginTop: 8 }}>
        <KV k="Subtotal productos" v={fmtMoney(detail.subtotal_cop)} />
        {detail.shipping_cop > 0 && (
          <KV
            k={detail.summary.shipping_confirmed ? "Envío" : "Envío (estimado)"}
            v={fmtMoney(detail.shipping_cop)}
          />
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

function partialMatchNote(item: OrderItemDetail): string {
  const chosen = item.selected_variant_title
    ? `Variante elegida: ${item.selected_variant_title}. `
    : "";
  const kinds = item.variant_unresolved_tag_kinds.join(" / ");
  const verify = kinds ? `(verificar ${kinds})` : "(verificar)";
  return `${chosen}Sin resolver: ${item.variant_unresolved_tokens.join(", ")} ${verify}`;
}

// Pedido #44: el precio de la línea ya trae el descuento del cupón (Medusa no
// lo ve como descuento), así que la línea explica de dónde sale.
function couponNote(item: OrderItemDetail): string {
  const off = item.discount_unit_cop > 0 ? `: −${fmtMoney(item.discount_unit_cop)} c/u` : "";
  const list =
    item.list_unit_price_cop != null
      ? ` (precio de lista ${fmtMoney(item.list_unit_price_cop)})`
      : "";
  return `Cupón ${item.coupon_code}${off}${list}`;
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
        {!item.variant_label_mismatch &&
          item.variant_match_kind === "partial" && (
            <div
              style={{
                fontSize: 10,
                color: "var(--fg-soft)",
                marginTop: 2,
                lineHeight: 1.35,
              }}
            >
              {partialMatchNote(item)}
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
        {item.coupon_code && (
          <div style={{ fontSize: 10, color: "var(--fg-soft)", lineHeight: 1.35 }}>
            {couponNote(item)}
          </div>
        )}
      </div>
      <div className="ir-t">{fmtMoney(item.total_cop)}</div>
    </div>
  );
}
