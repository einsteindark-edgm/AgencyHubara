import type {
  Order,
  OrderDetail,
} from "@plugins/orders/frontend/entities/order";
import { InsBlock, MissingData } from "@/shared/ui";
import { KV } from "./KV";

export function DeliveryPanel({
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
