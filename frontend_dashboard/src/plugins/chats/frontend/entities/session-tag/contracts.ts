/**
 * Contrato de `POST /api/chats/session-actions/{session_key}/operator-tag`
 * (botón "Reasignar" del inspector). El operador DECIDE el tag; el backend
 * no reconcilia (a diferencia del `/tag` que usan los agentes externos).
 */

import { z } from "zod";

/** Lo que el operador puede fijar a mano. HUMANO / CONFIRMADO_* son estados
 *  del flujo y COMPRA_EXITOSA lo pone "Confirmar pago" en Orders. */
export const OPERATOR_TAGS = ["INTERESADO", "RECHAZO", "REMARKETING"] as const;
export type OperatorTag = (typeof OPERATOR_TAGS)[number];

export const OPERATOR_TAG_LABELS: Record<OperatorTag, string> = {
  INTERESADO: "Interesado (el sistema decide si reactiva)",
  RECHAZO: "Rechazo (cierre sin venta, sin remarketing)",
  REMARKETING: "Remarketing (re-contactar ahora)",
};

export const operatorTagResponseSchema = z.object({
  tag: z.string(),
  motivo: z.string(),
  active_route: z.string(),
  episode_closed: z
    .object({ episode_id: z.string().nullable(), closing_tag: z.string() })
    .nullable(),
});

export type OperatorTagResponse = z.infer<typeof operatorTagResponseSchema>;

export interface ReassignTagInput {
  tag: OperatorTag;
  motivo: string;
}
