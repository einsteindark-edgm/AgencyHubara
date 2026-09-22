/**
 * Contrato de `POST|DELETE /api/chats/session-actions/{session_key}/postpone`
 * (botón "Posponer" del inspector). El operador fija el día en que el equipo
 * retoma el chat — también si lo tomó un humano. No toca la etiqueta ni la ruta.
 */

import { z } from "zod";
import { sessionPostponedSchema } from "@plugins/chats/frontend/entities/session";

export const postponeResponseSchema = z.object({
  postponed: sessionPostponedSchema.nullable(),
});

export type PostponeResponse = z.infer<typeof postponeResponseSchema>;

export interface PostponeInput {
  /** Día de la retoma, `YYYY-MM-DD` (el backend lo fija a las 10:00 locales). */
  date: string;
  /** Qué hay que retomar — se ve en la fila del filtro "Pospuestos". */
  note: string;
}
