import { useState } from "react";
import {
  PAY_STATUS_META,
  useConfirmOrderPayment,
  useReverseOrderPayment,
  type Order,
  type OrderDetail,
} from "@plugins/orders/frontend/entities/order";
import { fmtMoney } from "@/shared/lib";
import { InsBlock, MacButton, MissingData } from "@/shared/ui";
import { KV } from "./KV";

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "6px 8px",
  background: "rgba(255,255,255,0.04)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 4,
  color: "var(--fg)",
  fontSize: 12,
};

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
  const reversePayment = useReverseOrderPayment();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [reversing, setReversing] = useState(false);
  const [reason, setReason] = useState("");
  const isPaid = order.payStatus === "paid";
  // "Reversar pago" = corrección de un error operativo (confirmado por
  // error). También aplica a un refund hecho en Medusa Admin después de
  // confirmar: deja el pedido como pendiente y devuelve el chat a
  // "pago por verificar".
  const canReverse = order.payStatus !== "pending";

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

  const closeReversal = () => {
    setReversing(false);
    setReason("");
  };

  const onReverse = () => {
    setErrorMsg(null);
    reversePayment.mutate(
      { orderId: order.id, reason: reason.trim() || undefined },
      {
        onSuccess: (r) => {
          if (!r.success) setErrorMsg(r.error_detail ?? "No se pudo reversar el pago.");
          else closeReversal();
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
            Registra el pago manual en Medusa y lo marca como pagado. La
            conversación vuelve al bot de ventas.
          </p>
        </div>
      )}
      {canReverse && !reversing && (
        <div style={{ marginTop: 12 }}>
          <MacButton
            ghost
            sm
            onClick={() => {
              setErrorMsg(null);
              setReversing(true);
            }}
            style={{ color: "var(--color-danger)" }}
          >
            ↺ Reversar pago…
          </MacButton>
        </div>
      )}
      {canReverse && reversing && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>
            ¿Seguro? El pedido vuelve a <b>Pendiente de pago</b>: se registra un
            reembolso en Medusa (no mueve dinero) y la conversación vuelve a la
            bandeja humana para verificar el pago. Usalo solo para corregir un
            error operativo.
          </p>
          <input
            placeholder="Motivo (opcional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={inputStyle}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm onClick={closeReversal} disabled={reversePayment.isPending}>
              Volver
            </MacButton>
            <MacButton
              primary
              sm
              onClick={onReverse}
              disabled={reversePayment.isPending}
              style={{ background: "var(--color-danger)" }}
            >
              {reversePayment.isPending ? "Reversando…" : "Sí, reversar pago"}
            </MacButton>
          </div>
        </div>
      )}
      {errorMsg && (
        <div
          role="alert"
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
