/**
 * Modal "Reactivar conversación".
 *
 * Con la ventana de servicio 24h de WhatsApp cerrada, el humano no puede
 * mandar texto libre: solo una plantilla aprobada, hasta que el cliente
 * responda. El modal preselecciona la plantilla de seguimiento humano
 * (`is_default`), pide el texto de cada variable y muestra en vivo lo que
 * recibirá el cliente — el slot vacío se ve como `{ tu texto aquí }`.
 * Las demás plantillas del catálogo quedan en el selector.
 */

import { useMemo, useState } from "react";
import {
  buildTemplatePreview,
  isTemplateReady,
  sanitizeTemplateParam,
  useSendTemplateMessageMutation,
  useWhatsAppTemplates,
  type WhatsAppTemplate,
} from "@plugins/chats/frontend/entities/handoff";
import { apiErrorDetail } from "../model/apiErrorDetail";

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
  // Un id por intento de envío: un doble clic o retry no duplica la plantilla.
  const [clientMessageId] = useState(newClientMessageId);

  const selected =
    templates.find((t) => t.name === selectedName) ??
    templates.find((t) => t.is_default) ??
    templates[0];

  const preview = selected ? buildTemplatePreview(selected, values) : [];
  const ready = selected ? isTemplateReady(selected, values) : false;

  const submit = () => {
    if (!selected || !ready || !chatId || sendTemplate.isPending) return;
    const variables = Object.fromEntries(
      selected.variables.map((v) => [v.name, (values[v.name] ?? "").trim()]),
    );
    sendTemplate.mutate(
      { template_name: selected.name, variables, client_message_id: clientMessageId },
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
              const value = values[v.name] ?? "";
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

            <div className="rt-preview-wrap">
              <span className="rt-preview-label">Así lo verá el cliente</span>
              <div className="rt-preview" data-testid="template-preview">
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
            className="interv-btn"
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
