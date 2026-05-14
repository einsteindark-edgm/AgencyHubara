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
});

export type HandoffResponse = z.infer<typeof handoffResponseSchema>;
export type HumanMessageResponse = z.infer<typeof humanMessageResponseSchema>;

export type TargetRoute = "ventas" | "remarketing";

export interface ReturnToBotInput {
  target_route: TargetRoute;
  /** Requerido sólo si target_route === "remarketing". */
  motivo?: string;
}
