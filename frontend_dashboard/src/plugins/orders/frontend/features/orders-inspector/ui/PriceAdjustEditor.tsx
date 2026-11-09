/**
 * «Ajustar precio» (pedido #169, 2026-11-09): el operador escribe el precio
 * real por unidad de cada línea (precio negociado, p. ej. al por mayor).
 * Medusa queda con ese precio y Órdenes, Exportar y Ads leen el nuevo total.
 * Solo viajan las líneas que cambian, con el motivo (obligatorio).
 */
import { useState } from "react";
import {
  useAdjustOrderPrices,
  type Order,
  type OrderDetail,
  type OrderItemDetail,
} from "@plugins/orders/frontend/entities/order";
import { fmtMoney } from "@/shared/lib";
import { MacButton } from "@/shared/ui";

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

const noteStyle: React.CSSProperties = { fontSize: 11, color: "var(--fg-soft)", margin: 0, lineHeight: 1.4 };

/** «18.000», «$ 18000» → 18000; vacío o 0 → null. */
function parseCop(raw: string): number | null {
  const digits = raw.replace(/\D/g, "");
  const n = digits ? Number(digits) : 0;
  return n > 0 ? n : null;
}

type EditableLine = OrderItemDetail & { item_id: string };

export function PriceAdjustEditor({
  detail,
  order,
  onClose,
}: {
  detail: OrderDetail;
  order: Order;
  onClose: () => void;
}) {
  const adjust = useAdjustOrderPrices();
  const lines = detail.items_detail.filter((it): it is EditableLine => Boolean(it.item_id));
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(lines.map((it) => [it.item_id, String(it.unit_price_cop)])),
  );
  const [reason, setReason] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const parsed = lines.map((it) => ({ line: it, price: parseCop(draft[it.item_id] ?? "") }));
  const invalid = parsed.some((p) => p.price === null);
  const changes = parsed.flatMap(({ line, price }) =>
    price !== null && price !== line.unit_price_cop ? [{ line, price }] : [],
  );
  const delta = changes.reduce((acc, { line, price }) => acc + (price - line.unit_price_cop) * line.quantity, 0);
  const newTotal = detail.summary.total_cop + delta;
  const isPaid = order.payStatus === "paid";
  const raisesPaid = isPaid && changes.some(({ line, price }) => price > line.unit_price_cop);
  const canSave =
    changes.length > 0 && !invalid && !raisesPaid && reason.trim() !== "" && !adjust.isPending;

  const onSave = () => {
    setErrorMsg(null);
    adjust.mutate(
      {
        orderId: order.id,
        prices: changes.map(({ line, price }) => ({ item_id: line.item_id, unit_price_cop: price })),
        reason: reason.trim(),
      },
      {
        onSuccess: (r) => {
          if (!r.success) setErrorMsg(r.error_detail ?? "No se pudo ajustar el precio.");
          else onClose();
        },
        onError: (err) => setErrorMsg(err.message),
      },
    );
  };

  return (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <p style={noteStyle}>
        Escribe el precio por unidad que se negoció con el cliente. Medusa queda con este precio y
        Órdenes, Exportar y Ads usan el nuevo total.
      </p>
      {lines.map((it) => (
        <label key={it.item_id} style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 11 }}>
          <span style={{ color: "var(--fg-soft)" }}>
            {it.title} · {it.quantity} und
          </span>
          <input
            aria-label={`Precio por unidad de ${it.title}`}
            inputMode="numeric"
            value={draft[it.item_id] ?? ""}
            onChange={(e) => setDraft((d) => ({ ...d, [it.item_id]: e.target.value }))}
            style={inputStyle}
          />
        </label>
      ))}
      <input
        aria-label="Motivo del ajuste"
        placeholder="Motivo (por ejemplo, precio por mayor negociado)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        style={inputStyle}
      />
      {changes.length > 0 && !invalid && (
        <p style={{ ...noteStyle, color: "var(--fg)", fontWeight: 600 }}>Nuevo total: {fmtMoney(newTotal)}</p>
      )}
      {isPaid && (
        <p style={noteStyle}>
          El pedido ya está cobrado: si el total baja, la diferencia se registra en Medusa como un
          reembolso. No mueve dinero; corrige el cobro, que quedó con el precio de lista.
        </p>
      )}
      {raisesPaid && (
        <p style={{ ...noteStyle, color: "var(--color-warn)" }}>
          Para subir el precio de un pedido cobrado, primero reversa el pago y luego vuelve a confirmarlo.
        </p>
      )}
      {errorMsg && <p style={{ ...noteStyle, color: "var(--color-danger)" }}>{errorMsg}</p>}
      <div style={{ display: "flex", gap: 6 }}>
        <MacButton primary sm onClick={onSave} disabled={!canSave}>
          {adjust.isPending ? "Guardando…" : "Guardar precios"}
        </MacButton>
        <MacButton ghost sm onClick={onClose} disabled={adjust.isPending}>
          Cancelar
        </MacButton>
      </div>
    </div>
  );
}
