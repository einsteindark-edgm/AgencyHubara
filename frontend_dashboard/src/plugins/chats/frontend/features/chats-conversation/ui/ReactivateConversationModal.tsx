/**
 * Modal "Reactivar conversación".
 *
 * Con la ventana de servicio 24h de WhatsApp cerrada, el humano no puede
 * mandar texto libre: solo una plantilla aprobada, hasta que el cliente
 * responda. El modal preselecciona la plantilla de seguimiento humano
 * (`is_default`), pide el texto de cada variable y muestra en vivo lo que
 * recibirá el cliente — el slot vacío se ve como `{ tu texto aquí }`.
 * Las demás plantillas del catálogo quedan en el selector.
 *
 * Plantillas con `header_format === "image"` (pedido listo) llevan la FOTO del
 * pedido en el encabezado: el operador la adjunta acá, se sube a Meta como
 * cualquier foto del chat y el envío referencia su `attachment_id`. Hasta que
 * la foto no está subida no se puede enviar (Meta rechazaría la plantilla).
 * El selector es un botón visible ("Elegir foto del pedido", igual que el modal
 * de pedido listo de Órdenes) y el hueco de la foto en la vista previa también
 * lo abre: el `<input type="file">` nativo quedaba como texto gris sin forma de
 * botón y el operador no encontraba dónde adjuntar.
 *
 * Con el botón de enviar apagado, una línea dice qué falta (el número del
 * pedido, la foto): sin ella el operador adjuntaba la foto y no entendía por
 * qué no podía enviar.
 *
 * Se llena solo con el pedido del cliente (`useCustomerOrders`): si hay uno
 * listo, preselecciona «Pedido listo (con foto)»; el número, el monto y el
 * estado salen del pedido (`model/templatePrefill`); y si el operador ya subió
 * la foto en Órdenes, esa va en el encabezado (el envío manda
 * `header_order_photo_id` y el backend la sube a Meta). Todo se puede cambiar:
 * lo que escribe o elige el operador gana.
 */

import { useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import { apiFileUrl } from "@/shared/api";
import { compressImage, previewDataUrl } from "@/shared/lib";
import { Icon } from "@/shared/ui";
import {
  buildTemplatePreview,
  isTemplateReady,
  sanitizeTemplateParam,
  uploadHumanMedia,
  useSendTemplateMessageMutation,
  useWhatsAppTemplates,
  type WhatsAppTemplate,
} from "@plugins/chats/frontend/entities/handoff";
import {
  useCustomerOrders,
  useOrderRefPhoto,
} from "@plugins/chats/frontend/entities/order-ref";
import { apiErrorDetail } from "../model/apiErrorDetail";
import { orderTemplateDefaults, pickTemplateOrder } from "../model/templatePrefill";

/** Foto del encabezado (plantillas `header_format === "image"`). */
type HeaderPhoto =
  | { status: "empty" }
  | { status: "uploading"; previewUrl: string; progress: number }
  | { status: "ready"; previewUrl: string; attachmentId: string }
  | { status: "failed"; previewUrl: string; error: string };

type HeaderPhotoAction =
  | { type: "reset" }
  | { type: "start"; previewUrl: string }
  | { type: "progress"; progress: number }
  | { type: "uploaded"; attachmentId: string }
  | { type: "failed"; error: string };

function headerPhotoReducer(state: HeaderPhoto, action: HeaderPhotoAction): HeaderPhoto {
  switch (action.type) {
    case "reset":
      return { status: "empty" };
    case "start":
      return { status: "uploading", previewUrl: action.previewUrl, progress: 0 };
    case "progress":
      return state.status === "uploading" ? { ...state, progress: action.progress } : state;
    case "uploaded":
      return state.status === "uploading"
        ? { status: "ready", previewUrl: state.previewUrl, attachmentId: action.attachmentId }
        : state;
    case "failed":
      return {
        status: "failed",
        previewUrl: state.status === "empty" ? "" : state.previewUrl,
        error: action.error,
      };
  }
}

function revokeBlobUrl(url: string) {
  if (url.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
}

/** Comprime la foto; si el navegador no puede (formato raro, webview viejo),
 *  sube el archivo original — el backend igual valida tipo y tamaño. */
async function preparePhoto(file: File): Promise<{ blob: Blob; previewUrl: string }> {
  try {
    const { blob, previewUrl } = await compressImage(file);
    return { blob, previewUrl };
  } catch {
    return { blob: file, previewUrl: await previewDataUrl(file) };
  }
}

interface Props {
  chatId: string | null;
  onClose: () => void;
}

function newClientMessageId(): string {
  // randomUUID requiere secure context (https/localhost/tauri).
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `tpl-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Nombre para el operador. El catálogo trae ids técnicos; una plantilla nueva
 *  sin entrada acá igual se ve legible (id sin versión ni guiones bajos). */
const TEMPLATE_LABELS: Record<string, string> = {
  human_followup_utility_v1: "Seguimiento del equipo (mensaje libre)",
  order_ready_photo_utility_v1: "Pedido listo (con foto)",
  quote_ready_utility_v2: "Cotización lista",
  payment_pending_utility_v2: "Pago pendiente",
  order_status_utility_v2: "Estado del pedido",
  cart_recovery_marketing_v2: "Carrito pendiente",
  campaign_promo_marketing_v1: "Campaña promocional",
};

function templateLabel(t: WhatsAppTemplate): string {
  const pretty =
    TEMPLATE_LABELS[t.name] ??
    t.name.replace(/_v\d+$/, "").replace(/_(utility|marketing)$/, "").replace(/_/g, " ");
  const kind = t.category === "marketing" ? "marketing" : "utilidad";
  return `${pretty} · ${kind}${t.is_default ? " · recomendada" : ""}`;
}

export function ReactivateConversationModal({ chatId, onClose }: Props) {
  const templatesQuery = useWhatsAppTemplates(true);
  const sendTemplate = useSendTemplateMessageMutation(chatId);
  const templates = useMemo(
    () => (templatesQuery.data ?? []).filter((t) => t.body),
    [templatesQuery.data],
  );

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [photo, dispatchPhoto] = useReducer(headerPhotoReducer, { status: "empty" });
  const photoInputId = useId();
  // Intento vigente: descarta el resultado de una subida vieja si el operador
  // eligió otra foto (o cambió de plantilla) mientras subía.
  const photoAttempt = useRef(0);
  // Un id por intento de envío: un doble clic o retry no duplica la plantilla.
  const [clientMessageId] = useState(newClientMessageId);

  // Libera el blob de la preview al reemplazarla o al cerrar el modal.
  const photoPreviewUrl = photo.status === "empty" ? null : photo.previewUrl;
  useEffect(() => {
    if (!photoPreviewUrl) return;
    return () => revokeBlobUrl(photoPreviewUrl);
  }, [photoPreviewUrl]);

  // El pedido del cliente del que habla la plantilla (listo, si hay uno).
  const ordersQuery = useCustomerOrders(chatId);
  const order = pickTemplateOrder(ordersQuery.data?.orders);
  const defaults = orderTemplateDefaults(order);
  const readyPhotoTemplate =
    order?.status === "ready" ? templates.find((t) => t.header_format === "image") : undefined;

  const selected =
    templates.find((t) => t.name === selectedName) ??
    readyPhotoTemplate ??
    templates.find((t) => t.is_default) ??
    templates[0];

  const needsPhoto = selected?.header_format === "image";
  // La foto que el operador subió en Órdenes para ese pedido, si hay.
  const orderPhotoQuery = useOrderRefPhoto(order?.id ?? null, { enabled: needsPhoto });
  const orderPhoto = needsPhoto && order ? (orderPhotoQuery.data?.photo ?? null) : null;
  const usingOrderPhoto = photo.status === "empty" && orderPhoto !== null;
  const photoReady = photo.status === "ready" || usingOrderPhoto;

  // Lo que escribió el operador gana; si no tocó el campo, el dato del pedido.
  const filled: Record<string, string> = Object.fromEntries(
    (selected?.variables ?? []).map((v) => [
      v.name,
      values[v.name] ?? (defaults[v.name] ?? "").slice(0, v.max_length ?? undefined),
    ]),
  );
  const preview = selected ? buildTemplatePreview(selected, filled) : [];
  const ready =
    (selected ? isTemplateReady(selected, filled) : false) && (!needsPhoto || photoReady);
  // Qué le falta al envío, en palabras del operador (sin el ejemplo "(ej. #31)").
  const missing = selected
    ? [
        ...selected.variables
          .filter((v) => !(filled[v.name] ?? "").trim())
          .map((v) => (v.description ?? v.name).replace(/\s*\(.*\)\s*$/, ""))
          .map((label) => label.charAt(0).toLowerCase() + label.slice(1)),
        ...(needsPhoto && !photoReady
          ? [photo.status === "uploading" ? "que termine de subir la foto" : "la foto del pedido"]
          : []),
      ]
    : [];
  const previewSrc =
    photo.status !== "empty" && photo.previewUrl
      ? photo.previewUrl
      : usingOrderPhoto
        ? apiFileUrl(orderPhoto.file_url)
        : "";
  const previewPending = photo.status === "uploading" || photo.status === "failed";

  const resetPhoto = () => {
    photoAttempt.current += 1;
    dispatchPhoto({ type: "reset" });
  };

  const pickPhoto = async (file: File | undefined) => {
    if (!file || !chatId) return;
    const attempt = ++photoAttempt.current;
    const current = () => attempt === photoAttempt.current;
    try {
      const { blob, previewUrl } = await preparePhoto(file);
      if (!current()) return revokeBlobUrl(previewUrl);
      dispatchPhoto({ type: "start", previewUrl });
      const uploaded = await uploadHumanMedia(
        chatId,
        blob,
        `pedido-${Date.now()}.${blob.type === "image/png" ? "png" : "jpg"}`,
        (progress) => current() && dispatchPhoto({ type: "progress", progress }),
      );
      if (current()) dispatchPhoto({ type: "uploaded", attachmentId: uploaded.attachment_id });
    } catch (e) {
      if (current()) {
        dispatchPhoto({
          type: "failed",
          error: e instanceof Error ? e.message : "no se pudo subir la foto",
        });
      }
    }
  };

  const submit = () => {
    if (!selected || !ready || !chatId || sendTemplate.isPending) return;
    const variables = Object.fromEntries(
      selected.variables.map((v) => [v.name, (filled[v.name] ?? "").trim()]),
    );
    const header =
      needsPhoto && photo.status === "ready"
        ? { header_attachment_id: photo.attachmentId }
        : needsPhoto && usingOrderPhoto && order
          ? { header_order_photo_id: order.id }
          : {};
    sendTemplate.mutate(
      {
        template_name: selected.name,
        variables,
        client_message_id: clientMessageId,
        ...header,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <div
      className="return-picker-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="reactivate-title"
    >
      <div className="return-picker reactivate-modal" onClick={(e) => e.stopPropagation()}>
        <div className="rp-header">
          <b id="reactivate-title">Reactivar conversación</b>
          <button className="tb-btn" onClick={onClose} title="Cerrar">
            ✕
          </button>
        </div>
        <p className="rp-sub">
          Pasaron más de 24 h desde el último mensaje del cliente. WhatsApp solo
          permite escribirle con una plantilla aprobada. Cuando responda, sigues
          la conversación con normalidad.
        </p>

        {templatesQuery.isLoading && <p className="rp-sub">Cargando plantillas…</p>}
        {templatesQuery.isError && (
          <div className="composer-err" role="alert">
            No se pudieron cargar las plantillas: {apiErrorDetail(templatesQuery.error)}
          </div>
        )}

        {selected && (
          <>
            <label className="rt-field">
              <span>Plantilla</span>
              <select
                value={selected.name}
                onChange={(e) => {
                  setSelectedName(e.target.value);
                  setValues({});
                  resetPhoto();
                }}
              >
                {templates.map((t) => (
                  <option key={t.name} value={t.name}>
                    {templateLabel(t)}
                  </option>
                ))}
              </select>
            </label>
            {selected.category === "marketing" && (
              <p className="rt-warn">
                Plantilla de marketing: cuesta más que una de utilidad e incluye
                la opción de darse de baja.
              </p>
            )}

            {selected.variables.map((v) => {
              const value = filled[v.name] ?? "";
              return (
                <label key={v.name} className="rt-field">
                  <span>{v.description ?? v.name}</span>
                  <textarea
                    className="rp-motivo"
                    rows={selected.variables.length === 1 ? 3 : 1}
                    maxLength={v.max_length ?? undefined}
                    placeholder="Escribe aquí…"
                    value={value}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [v.name]: sanitizeTemplateParam(e.target.value),
                      }))
                    }
                  />
                  {v.max_length != null && (
                    <small className="rt-count">
                      {value.length}/{v.max_length}
                    </small>
                  )}
                </label>
              );
            })}

            {needsPhoto && (
              <div className="rt-field">
                <span>Foto del pedido</span>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="mac-btn sm cursor-pointer">
                    <Icon.attach />
                    {photo.status === "empty" && !usingOrderPhoto
                      ? "Elegir foto del pedido"
                      : "Cambiar foto"}
                    <input
                      id={photoInputId}
                      type="file"
                      accept="image/jpeg,image/png"
                      aria-label="Foto del pedido"
                      hidden
                      disabled={sendTemplate.isPending}
                      onChange={(e) => {
                        void pickPhoto(e.target.files?.[0]);
                        e.target.value = ""; // permite re-elegir la misma foto tras un fallo
                      }}
                    />
                  </label>
                  {photo.status === "uploading" && (
                    <small className="rt-photo-status">
                      Subiendo foto… {Math.round(photo.progress * 100)}%
                    </small>
                  )}
                  {photo.status === "ready" && (
                    <small className="rt-photo-status">Foto lista ✓</small>
                  )}
                  {usingOrderPhoto && (
                    <small className="rt-photo-status">La que subiste en Órdenes ✓</small>
                  )}
                </div>
              </div>
            )}
            {needsPhoto && photo.status === "failed" && (
              <div className="composer-err" role="alert">
                No se pudo subir la foto: {photo.error}. Elige la foto de nuevo.
              </div>
            )}

            <div className="rt-preview-wrap">
              <span className="rt-preview-label">Así lo verá el cliente</span>
              <div className="rt-preview" data-testid="template-preview">
                {needsPhoto &&
                  (previewSrc ? (
                    <img
                      src={previewSrc}
                      alt="Foto del pedido"
                      data-testid="template-preview-photo"
                      className={previewPending ? "rt-preview-photo pending" : "rt-preview-photo"}
                    />
                  ) : (
                    <label
                      htmlFor={photoInputId}
                      className="rt-preview-photo-empty cursor-pointer"
                      title="Elegir foto del pedido"
                    >
                      {"{ foto del pedido }"}
                    </label>
                  ))}
                {preview.map((seg, i) =>
                  seg.kind === "text" ? (
                    <span key={i}>{seg.text}</span>
                  ) : (
                    <mark
                      key={i}
                      className={seg.filled ? "rt-slot filled" : "rt-slot"}
                    >
                      {seg.text}
                    </mark>
                  ),
                )}
              </div>
            </div>
          </>
        )}

        {missing.length > 0 && (
          <p className="rt-warn" data-testid="template-missing">
            Para enviar falta: {missing.join(" y ")}.
          </p>
        )}
        {sendTemplate.isError && (
          <div className="composer-err" role="alert">
            No se pudo enviar la plantilla: {apiErrorDetail(sendTemplate.error)}
          </div>
        )}
        <div className="rp-actions">
          <button className="tb-btn" onClick={onClose}>
            Cancelar
          </button>
          <button
            className="interv-btn disabled:opacity-50"
            onClick={submit}
            disabled={!ready || sendTemplate.isPending}
          >
            {sendTemplate.isPending ? "Enviando…" : "Enviar plantilla"}
          </button>
        </div>
      </div>
    </div>
  );
}
