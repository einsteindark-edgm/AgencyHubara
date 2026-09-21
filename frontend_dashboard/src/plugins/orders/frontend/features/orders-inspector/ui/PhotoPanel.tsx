/**
 * "Foto del pedido" — panel del inspector de Órdenes.
 *
 * El operador sube la foto del pedido terminado. Al pasar el pedido a "listo"
 * el Agente ETA se la manda al cliente por WhatsApp con la plantilla de pedido
 * listo (foto en el encabezado; sirve aunque hayan pasado más de 24 h desde el
 * último mensaje del cliente). "Enviar ahora" la manda ya — o la reenvía tras
 * cambiarla — con confirmación en dos pasos: es un mensaje al cliente.
 *
 * Errores: se DERIVAN de las mutations (política de estado del frontend).
 */
import { useState, type ChangeEvent } from "react";
import {
  useDeleteOrderPhoto,
  useOrderPhoto,
  useSendOrderPhoto,
  useUploadOrderPhoto,
  type Order,
} from "@plugins/orders/frontend/entities/order";
import { ApiError, apiFileUrl } from "@/shared/api";
import { compressImage } from "@/shared/lib";
import { InsBlock, MacButton } from "@/shared/ui";

const errorBox: React.CSSProperties = {
  marginTop: 8,
  padding: 8,
  background: "rgba(255,114,105,0.12)",
  border: "1px solid rgba(255,114,105,0.3)",
  color: "var(--color-danger)",
  fontSize: 11,
  borderRadius: 4,
};

const hint: React.CSSProperties = { fontSize: 11, color: "var(--fg-muted)", margin: "6px 0 0" };

/** El detalle del backend (`{detail}`) es más útil que "API error 413". */
function errorText(error: Error): string {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
  }
  return error.message;
}

function newRequestId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `photo-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function formatSentAt(ms: number): string {
  return new Date(ms).toLocaleString("es-CO", {
    timeZone: "America/Bogota",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

const HIDDEN_STATUSES: ReadonlySet<Order["status"]> = new Set(["delivered", "cancelled"]);

export function PhotoPanel({ order }: { order: Order }) {
  const hidden = HIDDEN_STATUSES.has(order.status);
  const photoQuery = useOrderPhoto(hidden ? null : order.id);
  const upload = useUploadOrderPhoto();
  const remove = useDeleteOrderPhoto();
  const send = useSendOrderPhoto();
  const [confirming, setConfirming] = useState(false);

  if (hidden) return null;

  const data = photoQuery.data;
  const photo = data?.photo ?? null;
  const beforeReady = order.status === "new" || order.status === "preparing";

  const onPick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite volver a elegir la misma foto
    if (!file) return;
    let blob: Blob = file;
    try {
      blob = (await compressImage(file)).blob;
    } catch {
      // Sin compresión (formato raro): el backend valida tipo y tamaño.
    }
    setConfirming(false);
    upload.mutate({ orderId: order.id, file: blob });
  };

  const picker = (label: string) => (
    <label className="mac-btn mac-btn-ghost sm" style={{ cursor: "pointer" }}>
      {upload.isPending ? "Subiendo…" : label}
      <input
        type="file"
        accept="image/jpeg,image/png"
        aria-label={label}
        disabled={upload.isPending}
        onChange={(e) => void onPick(e)}
        style={{ display: "none" }}
      />
    </label>
  );

  const failure = upload.isError
    ? upload.error
    : remove.isError
      ? remove.error
      : send.isError
        ? send.error
        : null;

  return (
    <InsBlock title="Foto del pedido" open>
      {photoQuery.isLoading && <p style={hint}>Cargando foto…</p>}

      {data && !data.has_conversation && (
        <p style={hint}>
          Este pedido no está ligado a una conversación de WhatsApp: no hay a
          quién mandarle la foto.
        </p>
      )}

      {data?.has_conversation && !photo && (
        <>
          {picker("Subir foto del pedido")}
          <p style={hint}>
            {beforeReady
              ? "Al pasar el pedido a Listo se la mandamos al cliente por WhatsApp."
              : "El pedido ya está listo: sube la foto y usa Enviar ahora."}
          </p>
        </>
      )}

      {data?.has_conversation && photo && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <img
            src={apiFileUrl(photo.file_url)}
            alt="Foto del pedido"
            style={{
              width: "100%",
              maxHeight: 220,
              objectFit: "cover",
              borderRadius: 6,
              border: "1px solid rgba(255,255,255,0.08)",
            }}
          />
          <p style={{ ...hint, margin: 0 }}>
            {photo.sent_at_ms
              ? `Enviada al cliente · ${formatSentAt(photo.sent_at_ms)}`
              : beforeReady
                ? "Se enviará automáticamente al pasar el pedido a Listo."
                : "Aún no se ha enviado al cliente."}
          </p>

          {!confirming ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <MacButton primary sm onClick={() => setConfirming(true)} disabled={send.isPending}>
                {photo.sent_at_ms ? "Reenviar ahora" : "Enviar ahora"}
              </MacButton>
              {picker("Cambiar foto")}
              <MacButton
                ghost
                sm
                disabled={remove.isPending}
                onClick={() => remove.mutate({ orderId: order.id })}
              >
                Quitar
              </MacButton>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>
                ¿Mandar la foto a {order.customer} por WhatsApp? Le llega como
                "tu pedido está listo" con la foto.
              </p>
              <div style={{ display: "flex", gap: 6 }}>
                <MacButton ghost sm onClick={() => setConfirming(false)}>
                  Cancelar
                </MacButton>
                <MacButton
                  primary
                  sm
                  disabled={send.isPending}
                  onClick={() => {
                    send.mutate({ orderId: order.id, requestId: newRequestId() });
                    setConfirming(false);
                  }}
                >
                  Sí, enviar
                </MacButton>
              </div>
            </div>
          )}
          {send.isSuccess && !confirming && (
            <p style={{ ...hint, margin: 0 }}>
              En cola: el Agente ETA la manda por WhatsApp en unos segundos.
            </p>
          )}
        </div>
      )}

      {(failure || photo?.last_error) && (
        <div role="alert" style={errorBox}>
          {failure ? errorText(failure) : photo?.last_error}
        </div>
      )}
    </InsBlock>
  );
}
