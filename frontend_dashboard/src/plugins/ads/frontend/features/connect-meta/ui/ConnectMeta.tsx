/**
 * Botón "Conectar con Meta" + estado de la conexión OAuth.
 *
 * - Sin conexión → botón que NAVEGA a `/api/ads/meta/login` (el backend hace
 *   307 al diálogo de Meta; al volver, `?meta=connected` recarga la SPA y el
 *   `useMetaConnection` fetchea fresco → muestra conectado).
 * - Conectado → chip con la cuenta + expiración + "Desconectar" con confirm
 *   inline de dos pasos (regla 6: cero `window.confirm` en webviews Tauri).
 *
 * UI state local colocado (`useState`), server state SOLO en TanStack Query.
 */

import { useState } from "react";

import {
  metaLoginUrl,
  useDisconnectMeta,
  useMetaConnection,
} from "@plugins/ads/frontend/entities/meta-connection";

export function ConnectMeta() {
  const { data: conn, isLoading } = useMetaConnection();
  const disconnect = useDisconnectMeta();
  const [confirming, setConfirming] = useState(false);

  if (isLoading) {
    return <span className="text-xs text-gray-500">Verificando conexión…</span>;
  }

  if (!conn?.connected) {
    return (
      <button
        type="button"
        onClick={() => {
          window.location.href = metaLoginUrl();
        }}
        className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        Conectar con Meta
      </button>
    );
  }

  // Token long-lived expirado (~60d): no hay refresh automático → ofrecer reconectar
  // en vez de dejar que los insights fallen con errores opacos de Graph (premortem #5).
  if (conn.expired) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-800">
          Conexión Meta expirada
        </span>
        <button
          type="button"
          onClick={() => {
            window.location.href = metaLoginUrl();
          }}
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          Reconectar
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-1 text-xs font-medium text-green-800">
        <span aria-hidden>●</span>
        Meta conectado
        {conn.accountName ? ` · ${conn.accountName}` : ""}
      </span>
      {conn.expiresAt ? (
        <span className="text-xs text-gray-500">
          expira{" "}
          {new Date(conn.expiresAt * 1000).toLocaleDateString("es-CO", {
            day: "numeric",
            month: "short",
            year: "numeric",
          })}
        </span>
      ) : null}
      {confirming ? (
        <span className="flex items-center gap-2">
          <button
            type="button"
            disabled={disconnect.isPending}
            onClick={() =>
              disconnect.mutate(undefined, { onSettled: () => setConfirming(false) })
            }
            className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
          >
            {disconnect.isPending ? "Desconectando…" : "Confirmar"}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="rounded px-2 py-1 text-xs text-gray-500 hover:text-gray-800"
          >
            Cancelar
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="rounded px-2 py-1 text-xs text-gray-500 hover:text-gray-800"
        >
          Desconectar
        </button>
      )}
    </div>
  );
}
