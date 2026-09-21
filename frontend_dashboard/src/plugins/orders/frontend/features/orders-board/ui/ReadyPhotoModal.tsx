/**
 * Modal "Lista" — aparece al soltar un pedido en la columna `ready`.
 *
 * El operador puede adjuntar la foto del pedido terminado (opcional). Si la
 * sube, primero se guarda y SOLO si eso sale bien el pedido pasa a Lista: el
 * Agente ETA le manda la foto al cliente de una vez. El canal lo decide la
 * ventana de servicio de 24 h de WhatsApp y el modal lo avisa antes de mover:
 *  - abierta (el cliente escribió hace < 24 h) → mensaje normal con la foto;
 *  - cerrada → la plantilla aprobada de pedido listo (con la foto arriba).
 * Sin foto, el pedido pasa a Lista con el aviso de siempre.
 *
 * Overlay propio (regla #6: cero diálogos JS nativos), mismo patrón que el
 * modal de la guía de "En camino": backdrop + `role="dialog"` + Escape.
 * El error de la subida se DERIVA de la mutation (política de estado).
 */
import { useEffect, useState, type ChangeEvent } from "react";
import {
  useOrderPhoto,
  useUploadOrderPhoto,
} from "@plugins/orders/frontend/entities/order";
import { ApiError, apiFileUrl } from "@/shared/api";
import { compressImage } from "@/shared/lib";
import { MacButton } from "@/shared/ui";

interface Props {
  /** Id visible del pedido (`#1247`). */
  orderId: string;
  /** Transición en vuelo: deshabilita las acciones. */
  busy?: boolean;
  /** Mover a Lista (la foto, si la hay, ya quedó guardada). */
  onConfirm: () => void;
  onCancel: () => void;
}

interface Picked {
  blob: Blob;
  previewUrl: string;
}

function errorText(error: Error): string {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
  }
  return error.message;
}

export function ReadyPhotoModal({ orderId, busy = false, onConfirm, onCancel }: Props) {
  const photoQuery = useOrderPhoto(orderId);
  const upload = useUploadOrderPhoto();
  const [picked, setPicked] = useState<Picked | null>(null);

  // Escape cierra — listener global mientras el modal está montado.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Libera el blob de la preview al cambiarla o al cerrar.
  useEffect(() => {
    const url = picked?.previewUrl;
    return () => {
      if (url?.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(url);
      }
    };
  }, [picked?.previewUrl]);

  const data = photoQuery.data;
  const existing = data?.photo ?? null;
  const canSendPhoto = data?.has_conversation !== false;
  const hasPhoto = picked !== null || existing !== null;
  const working = busy || upload.isPending;

  const onPick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite volver a elegir la misma foto
    if (!file) return;
    try {
      const { blob, previewUrl } = await compressImage(file);
      setPicked({ blob, previewUrl });
    } catch {
      // Sin compresión (formato raro): el backend valida tipo y tamaño.
      setPicked({ blob: file, previewUrl: "" });
    }
  };

  const confirmWithPhoto = async () => {
    if (working) return;
    if (picked) {
      try {
        await upload.mutateAsync({ orderId, file: picked.blob });
      } catch {
        return; // el error lo pinta la mutation; el pedido NO se mueve
      }
    }
    onConfirm();
  };

  const channelNote = !canSendPhoto
    ? "Este pedido no está ligado a una conversación de WhatsApp: pasa a Lista sin enviar foto."
    : data?.service_window_open
      ? "El cliente escribió en las últimas 24 h: la foto le llega como mensaje normal."
      : "Pasaron más de 24 h desde el último mensaje del cliente: la foto le llega con la plantilla aprobada de pedido listo.";

  const previewSrc = picked?.previewUrl || (existing ? apiFileUrl(existing.file_url) : "");

  return (
    <div
      data-testid="ready-photo-backdrop"
      onClick={onCancel}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Marcar lista el pedido ${orderId}`}
        onClick={(e) => e.stopPropagation()}
        className="flex w-[460px] max-w-full flex-col gap-3 rounded-xl border border-line bg-canvas p-4 shadow-2xl"
      >
        <div>
          <div className="text-[13px] font-bold text-fg">📦 Pedido listo · {orderId}</div>
          <p className="mt-1 text-[11.5px] leading-snug text-fg-faint">
            Si adjuntas la foto del pedido, se la mandamos al cliente por
            WhatsApp apenas pase a Lista. Es opcional: sin foto recibe el aviso
            de siempre.
          </p>
        </div>

        {photoQuery.isLoading && <p className="text-[11px] text-fg-faint">Cargando…</p>}

        {data && canSendPhoto && (
          <div className="flex flex-col gap-2">
            {previewSrc ? (
              <img
                src={previewSrc}
                alt="Foto del pedido"
                className="max-h-56 w-full rounded-md border border-line object-cover"
              />
            ) : picked ? (
              <p className="text-[11px] text-fg-faint">Foto lista para subir.</p>
            ) : null}
            <label className="mac-btn mac-btn-ghost sm self-start" style={{ cursor: "pointer" }}>
              {hasPhoto ? "Cambiar foto" : "Elegir foto del pedido"}
              <input
                type="file"
                accept="image/jpeg,image/png"
                aria-label="Foto del pedido"
                disabled={working}
                onChange={(e) => void onPick(e)}
                style={{ display: "none" }}
              />
            </label>
          </div>
        )}

        {data && <p className="text-[11px] leading-snug text-fg-soft">{channelNote}</p>}

        {upload.isError && upload.error && (
          <span role="alert" className="text-[11px] text-danger">
            No se pudo subir la foto: {errorText(upload.error)}
          </span>
        )}

        <div className="flex items-center justify-end gap-2">
          <MacButton ghost sm type="button" onClick={onCancel} disabled={working}>
            Cancelar
          </MacButton>
          {!existing && (
            <MacButton sm type="button" onClick={onConfirm} disabled={working}>
              Pasar a Lista sin foto
            </MacButton>
          )}
          {canSendPhoto && (
            <MacButton
              primary
              sm
              type="button"
              onClick={() => void confirmWithPhoto()}
              disabled={working || !hasPhoto}
            >
              {upload.isPending ? "Subiendo foto…" : "Pasar a Lista y enviar foto"}
            </MacButton>
          )}
        </div>
      </div>
    </div>
  );
}
