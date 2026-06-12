/**
 * Reconciliación de pedidos pendientes (Premortem F2+K1): banner + filas con
 * acciones para pedidos que están en el vault local pero NO en Medusa
 * (rechazados o stub). Sin esta surface el operador podría perder ventas
 * silenciosamente.
 *
 * Extraído de `OrdersSection` (F5.2, auditoría 2026-06-10): es una feature
 * con lógica de negocio propia (mutations de retry/resolve), no composición
 * de página.
 *
 * Cada fila tiene sus propios mutation hooks (estado per-fila): así el
 * spinner de "Reintentando…" afecta solo la fila tocada. "Reintentar" usa el
 * mismo núcleo idempotente que el barrido automático del backend; "Marcar
 * resuelto" saca el record del banner sin tocar Medusa (el operador ya lo
 * registró a mano). Tras resolver, la invalidación de `orderKeys.vault()`
 * hace que la fila desaparezca.
 */

import { useState } from "react";

import {
  useRetryVaultOrder,
  useResolveVaultOrder,
  type VaultOrderRecord,
} from "@plugins/orders/frontend/entities/order";
import { Icon } from "@/shared/ui";

interface VaultOrdersBannerProps {
  failedCount: number;
  stubCount: number;
  records: VaultOrderRecord[];
}

export function VaultOrdersBanner({
  failedCount,
  stubCount,
  records,
}: VaultOrdersBannerProps) {
  return (
    <div
      style={{
        margin: "0 16px 12px",
        padding: 12,
        borderRadius: 8,
        background: "rgba(255,180,74,0.08)",
        border: "1px solid rgba(255,180,74,0.35)",
        color: "var(--fg-soft)",
        fontSize: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          color: "var(--color-warn)",
          fontWeight: 600,
        }}
      >
        <Icon.alert />
        <span>
          {records.length} pedido{records.length === 1 ? "" : "s"} pendiente
          {records.length === 1 ? "" : "s"} de reconciliar
          {failedCount > 0 && stubCount > 0
            ? ` (${failedCount} rechazado${failedCount === 1 ? "" : "s"} por Medusa, ${stubCount} local${stubCount === 1 ? "" : "es"})`
            : failedCount > 0
              ? ` (rechazado${failedCount === 1 ? "" : "s"} por Medusa)`
              : ` (registrado${stubCount === 1 ? "" : "s"} localmente sin Medusa)`}
        </span>
      </div>
      <details>
        <summary
          style={{
            cursor: "pointer",
            marginBottom: 6,
            color: "var(--fg-muted)",
          }}
        >
          Ver detalle
        </summary>
        <table
          style={{
            width: "100%",
            fontSize: 11,
            marginTop: 8,
            borderCollapse: "collapse",
          }}
        >
          <thead>
            <tr style={{ textAlign: "left", color: "var(--fg-muted)" }}>
              <th style={{ padding: "4px 8px" }}>Tipo</th>
              <th style={{ padding: "4px 8px" }}>Sesión</th>
              <th style={{ padding: "4px 8px" }}>Cliente</th>
              <th style={{ padding: "4px 8px" }}>Total</th>
              <th style={{ padding: "4px 8px" }}>Estado</th>
              <th style={{ padding: "4px 8px" }}>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {records.slice(0, 10).map((r) => (
              <VaultOrderRow key={r.order_id} r={r} />
            ))}
          </tbody>
        </table>
        {records.length > 10 && (
          <div style={{ marginTop: 6, color: "var(--fg-muted)" }}>
            +{records.length - 10} más…
          </div>
        )}
      </details>
    </div>
  );
}

// Estilo compartido de los botones de acción de la fila (mismo idioma inline
// del resto del banner; la migración a tokens es F4).
const vaultActionBtnStyle = (busy: boolean): React.CSSProperties => ({
  fontSize: 10,
  padding: "2px 8px",
  borderRadius: 4,
  border: "1px solid rgba(255,255,255,0.14)",
  background: "transparent",
  color: "var(--fg-soft)",
  cursor: busy ? "wait" : "pointer",
  opacity: busy ? 0.5 : 1,
});

function VaultOrderRow({ r }: { r: VaultOrderRecord }) {
  const retry = useRetryVaultOrder();
  const resolve = useResolveVaultOrder();
  // Confirmación inline en dos pasos (patrón DangerPanel del inspector).
  // `window.confirm` quedó prohibido: en los webviews de Tauri (WKWebView)
  // los diálogos JS nativos no son confiables — el operador podía quedar
  // sin forma de confirmar (auditoría 2026-06-10, F0.6).
  const [confirmingResolve, setConfirmingResolve] = useState(false);
  const busy = retry.isPending || resolve.isPending;
  const outcome = retry.data?.outcome;

  const onResolve = () => {
    setConfirmingResolve(false);
    resolve.mutate({ sessionKey: r.session_key, auditId: r.order_id });
  };

  let estado = r.status === "abandoned" ? "Abandonado" : "Pendiente";
  if (retry.isError || resolve.isError) estado = "Error de red";
  else if (outcome === "still_failing") estado = "Medusa sigue caído";

  return (
    <tr style={{ borderTop: "1px solid rgba(255,255,255,0.04)" }}>
      <td style={{ padding: "4px 8px" }}>
        <span
          style={{
            padding: "1px 5px",
            borderRadius: 3,
            background:
              r.kind === "failed"
                ? "var(--color-danger-soft)"
                : "var(--color-violet-soft)",
            color: r.kind === "failed" ? "var(--color-danger)" : "var(--color-violet)",
            fontSize: 9,
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          {r.kind === "failed" ? "Rechazado" : "Local (stub)"}
        </span>
      </td>
      <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono)" }}>
        {r.session_key}
      </td>
      <td style={{ padding: "4px 8px" }}>
        {r.customer_phone ?? "—"} · {r.customer_city ?? "—"}
      </td>
      <td style={{ padding: "4px 8px" }}>
        ${r.total_cop.toLocaleString("es-CO")} {r.currency}
      </td>
      <td
        style={{
          padding: "4px 8px",
          color: r.status === "abandoned" ? "#ff4d4d" : "var(--fg-muted)",
          maxWidth: 200,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={r.error_detail ?? ""}
      >
        {estado}
        {r.attempts > 0
          ? ` · ${r.attempts} intento${r.attempts === 1 ? "" : "s"}`
          : ""}
      </td>
      <td style={{ padding: "4px 8px", whiteSpace: "nowrap" }}>
        {confirmingResolve ? (
          <>
            <span style={{ marginRight: 6, color: "var(--fg-soft)" }}>
              ¿Ya lo registraste en Medusa Admin?
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={onResolve}
              style={{
                ...vaultActionBtnStyle(busy),
                color: "var(--color-ok)",
                borderColor: "rgba(91,224,123,0.4)",
                marginRight: 4,
              }}
            >
              Sí, marcar resuelto
            </button>
            <button
              type="button"
              onClick={() => setConfirmingResolve(false)}
              style={vaultActionBtnStyle(false)}
            >
              Volver
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                retry.mutate({ sessionKey: r.session_key, auditId: r.order_id })
              }
              style={{ ...vaultActionBtnStyle(busy), marginRight: 4 }}
            >
              {retry.isPending ? "Reintentando…" : "Reintentar"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmingResolve(true)}
              style={vaultActionBtnStyle(busy)}
            >
              Marcar resuelto
            </button>
          </>
        )}
      </td>
    </tr>
  );
}
