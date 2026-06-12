import { useState } from "react";
import { useCancelOrder, type Order } from "@plugins/orders/frontend/entities/order";
import { InsBlock, MacButton } from "@/shared/ui";

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

export function DangerPanel({ order }: { order: Order }) {
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
          style={{ color: "var(--color-danger)" }}
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
              style={{ background: "var(--color-danger)", borderColor: "var(--color-danger)" }}
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
                color: "var(--color-danger)",
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
