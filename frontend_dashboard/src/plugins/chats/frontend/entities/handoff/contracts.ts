/**
 * Schemas Zod de las respuestas del backend handoff
 * (`src/dashboard/handoff.py`). Validamos en el boundary para que un cambio
 * de contrato truene temprano y con mensaje claro.
 */

import { z } from "zod";

export const handoffResponseSchema = z.object({
  ok: z.boolean(),
  active_route: z.string(),
  tag: z.string(),
  motivo: z.string(),
  terminated_workflows: z.array(z.string()).default([]),
});

export const humanMessageResponseSchema = z.object({
  ok: z.boolean(),
  role: z.string(),
  sender: z.string(),
  content: z.string(),
  /** Presente cuando el operador mandó una foto — ref servible por el dashboard. */
  image_url: z.string().nullable().optional(),
  /** Presentes cuando el adjunto enviado fue un documento (PDF): ref servible
   *  + nombre visible del archivo. */
  document_url: z.string().nullable().optional(),
  document_filename: z.string().nullable().optional(),
});

/** Respuesta de la fase A (subida): el media_id de Meta + la url servible. */
export const mediaUploadResponseSchema = z.object({
  ok: z.boolean(),
  attachment_id: z.string(),
  media_ref: z.string(),
});

export type HandoffResponse = z.infer<typeof handoffResponseSchema>;
export type HumanMessageResponse = z.infer<typeof humanMessageResponseSchema>;
export type MediaUploadResponse = z.infer<typeof mediaUploadResponseSchema>;

/** Variables del envío del operador: al menos uno de `text` / `attachment_id`.
 *  `client_message_id` da idempotencia (un retry no re-envía). */
export interface SendHumanMessageInput {
  text?: string;
  attachment_id?: string;
  client_message_id?: string;
}

export type TargetRoute = "ventas" | "remarketing";

export interface ReturnToBotInput {
  target_route: TargetRoute;
  /** Requerido sólo si target_route === "remarketing". */
  motivo?: string;
}
