import { useState } from "react";
import {
  PAY_STATUS_META,
  useConfirmOrderPayment,
  type Order,
  type OrderDetail,
} from "@plugins/orders/frontend/entities/order";
import { fmtMoney } from "@/shared/lib";
import { InsBlock, MacButton, MissingData } from "@/shared/ui";
import { KV } from "./KV";

export function PaymentPanel({
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
            color: "var(--color-danger)",
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
