import { z } from "zod";

/**
 * Frecuencia del remarketing — `/api/agents/remarketing/frequency`, cast al
 * contrato `remarketing-frequency@v1` de chats (`chats/api/remarketing_frequency.py`).
 *
 * Tolerante en lo accesorio (L-10: la escalera y la autoría degradan a un
 * neutro) pero NO en la cantidad: si `max_touches` o `ceiling` no vienen, el
 * parse falla y el panel no se monta — mejor un error que mostrar un «0
 * toques» inventado que el operador podría tomar por real.
 */

export const ladderStepSchema = z.object({
  touch: z.number(),
  /** Cuánto después del último mensaje del cliente sale este toque (acumulado). */
  after_ms: z.number(),
});

export const frequencySchema = z.object({
  /** La cantidad que corre de verdad: lo guardado, dentro del techo. */
  max_touches: z.number().int(),
  /** Techo de Terraform: el dashboard nunca lo supera. */
  ceiling: z.number().int(),
  /** Lo guardado por el dashboard (null = nunca se tocó → manda el techo). */
  saved: z.number().int().nullable().catch(null).default(null),
  updated_at_ms: z.number().nullable().catch(null).default(null),
  updated_by: z.string().nullable().catch(null).default(null),
  ladder: z.array(ladderStepSchema).catch([]).default([]),
});
