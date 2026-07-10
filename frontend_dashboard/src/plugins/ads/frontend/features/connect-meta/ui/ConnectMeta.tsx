/**
 * Estado de la conexión a Meta — SOLO lectura (single-tenant, decisión 2026-07-09).
 *
 * La conexión se provisiona server-side: un system-user token (no expira) sembrado
 * en SSM `/hubara/<tenant>/meta/oauth` por el operador (runbook en
 * `infra/whatsapp-provisioning/README.md` §ads-token). No hay diálogo OAuth ni
 * "Desconectar": conectar/rotar el token es una operación de infra, no de UI.
 */

import { useMetaConnection } from "@plugins/ads/frontend/entities/meta-connection";

export function ConnectMeta() {
  const { data: conn, isLoading } = useMetaConnection();

  if (isLoading) {
    return <span className="text-xs text-gray-500">Verificando conexión…</span>;
  }

  if (!conn?.connected) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">
          <span aria-hidden>○</span>
          Meta no conectado
        </span>
        <span className="text-xs text-gray-500">
          provisionar el token (runbook infra/whatsapp-provisioning)
        </span>
      </div>
    );
  }

  // El token provisionado es un system-user (expires_at null → nunca entra acá).
  // Si algún día se siembra un token con expiración y vence, se señala — el fix
  // es re-provisionar desde infra, no un botón.
  if (conn.expired) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-800">
        Conexión Meta expirada — re-provisionar el token (infra)
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-1 text-xs font-medium text-green-800">
      <span aria-hidden>●</span>
      Meta conectado
      {conn.accountName ? ` · ${conn.accountName}` : ""}
    </span>
  );
}
