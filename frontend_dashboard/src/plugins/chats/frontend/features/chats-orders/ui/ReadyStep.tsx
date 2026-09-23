/**
 * Paso "Marcar listo" del panel móvil — espejo del modal "Lista" del tablero.
 *
 * El operador puede adjuntar la foto del pedido terminado (opcional). Si la
 * elige, primero se sube y SOLO si eso sale bien el pedido pasa a Lista: el
 * Agente ETA le manda la foto al cliente. El canal lo decide la ventana de
 * 24 h de WhatsApp y el paso lo avisa antes de mover. Sin foto, el pedido pasa
 * a Lista con el aviso de siempre.
 *
 * Va EN LÍNEA dentro de la tarjeta (no un modal): el panel ya vive en un
 * bottom-sheet y un segundo overlay encima es incómodo en el celular.
 */
import { useEffect, useState, type ChangeEvent } from "react";

import {
  useOrderRefPhoto,
  useUploadOrderRefPhoto,
} from "@plugins/chats/frontend/entities/order-ref";
import { ApiError, apiFileUrl } from "@/shared/api";
import { compressImage } from "@/shared/lib";

interface Props {
  orderId: string;
  /** Transición en vuelo: deshabilita las acciones. */
  busy: boolean;
  /** Mover a Lista (la foto, si la hay, ya quedó guardada). */
  onConfirm: () => void;
  onCancel: () => void;
}

interface Picked {
  blob: Blob;
  previewUrl: string;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
  }
  return error instanceof Error ? error.message : "Error de red.";
}

export function ReadyStep({ orderId, busy, onConfirm, onCancel }: Props) {
  const photoQuery = useOrderRefPhoto(orderId);
  const upload = useUploadOrderRefPhoto();
  const [picked, setPicked] = useState<Picked | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

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
  const working = busy || uploading;

  const onPick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite volver a elegir la misma foto
    if (!file) return;
    setUploadError(null);
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
      setUploading(true);
      setUploadError(null);
      try {
        await upload.mutateAsync({ orderId, file: picked.blob });
      } catch (err) {
        setUploadError(errorText(err));
        return; // el pedido NO se mueve
      } finally {
        setUploading(false);
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
    <div className="order-step" aria-label="Marcar listo">
      <p className="order-step-hint">
        Si adjuntas la foto del pedido, se la mandamos al cliente por WhatsApp
        apenas pase a Lista. Es opcional.
      </p>

      {photoQuery.isLoading && <p className="order-step-hint">Cargando…</p>}

      {data && canSendPhoto && (
        <>
          {previewSrc ? (
            <img src={previewSrc} alt="Foto del pedido" className="order-step-photo" />
          ) : picked ? (
            <p className="order-step-hint">Foto lista para subir.</p>
          ) : null}
          <label className="order-ghost-btn order-step-pick">
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
        </>
      )}

      {data && <p className="order-step-note">{channelNote}</p>}

      {uploadError && (
        <div className="order-card-err" role="alert">
          No se pudo subir la foto: {uploadError}
        </div>
      )}

      <div className="order-card-actions">
        {canSendPhoto && (
          <button
            className="order-advance-btn"
            disabled={working || !hasPhoto}
            onClick={() => void confirmWithPhoto()}
          >
            {uploading ? "Subiendo foto…" : "Pasar a Lista y enviar foto"}
          </button>
        )}
        {!existing && (
          <button className="order-ghost-btn" disabled={working} onClick={onConfirm}>
            Pasar a Lista sin foto
          </button>
        )}
        <button className="order-ghost-btn" disabled={working} onClick={onCancel}>
          Volver
        </button>
      </div>
    </div>
  );
}
