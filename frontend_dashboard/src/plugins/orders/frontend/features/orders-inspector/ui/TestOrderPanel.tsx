import { useState } from "react";
import { useSetTestOrder, type Order } from "@plugins/orders/frontend/entities/order";
import { MacButton } from "@/shared/ui";

/* ── TestOrderPanel — marcar / desmarcar el pedido como "prueba" ───────── */

/**
 * Un pedido de prueba sigue en el tablero pero no cuenta en ningún número
 * (Órdenes, Ads, campañas) ni se reporta a Meta. La marca vive en Medusa
 * (`hubara_test_order`) y es reversible, así que no pide confirmación.
 */
export function TestOrderPanel({ order }: { order: Order }) {
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const setTest = useSetTestOrder();
  const isTest = order.isTest === true;

  const toggle = () => {
    setErrorMsg(null);
    setTest.mutate(
      { orderId: order.id, isTest: !isTest },
      {
        onSuccess: (r) => {
          if (!r.success && r.error_detail) setErrorMsg(r.error_detail);
        },
        onError: (err) => setErrorMsg(err.message),
      },
    );
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "10px 16px",
        borderBottom: "0.5px solid var(--line)",
        ...(isTest ? { background: "var(--color-neutral-soft)" } : {}),
      }}
    >
      {isTest && (
        <p style={{ margin: 0, fontSize: 11, color: "var(--fg-soft)" }}>
          <b>Pedido de prueba</b> — no cuenta en los totales ni se reporta a Meta.
        </p>
      )}
      <MacButton ghost sm onClick={toggle} disabled={setTest.isPending}>
        {setTest.isPending
          ? "Guardando…"
          : isTest
            ? "Quitar marca de prueba"
            : "Marcar como prueba"}
      </MacButton>
      {errorMsg && (
        <div style={{ fontSize: 11, color: "var(--color-danger)" }}>{errorMsg}</div>
      )}
    </div>
  );
}
